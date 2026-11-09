"""Replay engine: feeds the article stream into every counter and keeps the
state the dashboard and the Solar agent read.

Per article:
  * pruned decaying window   -> the ranking we show (memory-saving method)
  * exact decaying window    -> baseline that keeps every keyword (for comparison)
  * DGIM + exact window count for each *tracked* keyword (secondary feature)
  * inverted index for related-article lookup (reported separately from the
    counting memory, as the proposal requires)

Terminology used by the API and the UI
  * "진입" (row badge)     : in Top K now, not in Top K on the previous screen update
  * "첫 진입" event        : first time in Top K since this replay started (warm-up excluded)
  * "재진입" event         : back in Top K after >= new_entry_gap snapshots outside it

DGIM tracking policy (v0.2)
  * capacity ``max_tracked`` applies to automatic *and* user tracking
  * user-pinned keywords and keywords currently in Top K are never evicted
  * when full, the automatic tracker that left Top K longest ago is evicted;
    if it is tracked again later, counting restarts and ``generation`` increases
  * user pins are limited to half the capacity so automatic tracking keeps working
"""
from __future__ import annotations

import math
import time
from bisect import bisect_left, bisect_right
from collections import Counter, deque
from dataclasses import asdict, dataclass
from datetime import date as _date

from .data import Article, first_index_on_or_after
from .decaying import DecayingWindow
from .dgim import DGIM
from .ranking import ABSORB_RATIO, absorb, burst_scores, outside_candidates, uncertainty
from .sliding import ExactWindowCount


@dataclass
class EngineConfig:
    c: float = 1e-3              # decay constant; half-life = ln 0.5 / ln(1-c) (~693 articles)
    threshold: float = 0.5       # pruning threshold of the decaying window
    k: int = 20                  # ranking size
    dgim_n: int = 1000           # DGIM window (last N articles)
    dgim_r: int = 2              # max buckets per size
    snapshot_every: int = 50     # articles between ranking snapshots
    new_entry_gap: int = 40      # snapshots outside Top K before a return counts as "재진입"
    max_tracked: int = 300       # DGIM tracking capacity (auto + user)
    warmup_factor: float = 10.0  # warm-up of warmup_factor / c decay steps before start
    # v0.3 ---------------------------------------------------------------------
    phrases: bool = True         # also count adjacent keyword pairs ("donald trump")
    absorb_ratio: float = ABSORB_RATIO   # hide a word if a phrase with it has >= ratio x its score
    decay_unit: str = "day"      # "day": per calendar day (v0.3 default, see E11) | "article": per article (c)
    half_life_days: float = 7.0  # used when decay_unit == "day"
    burst_ratio: float = 7.0     # long half-life / short half-life for burst detection
    burst_min: float = 3.0       # minimum short-window score for a burst candidate
    # v0.4 ---------------------------------------------------------------------
    min_evidence: int = 2        # a Top K row with <= this many recent articles is "thin" (E8)

    # (min, max) inclusive ranges; checked before the engine state is touched
    _RANGES = {
        "c": (1e-5, 0.5), "threshold": (0.01, 0.99), "k": (1, 100),
        "dgim_n": (10, 100_000), "dgim_r": (2, 64), "snapshot_every": (1, 10_000),
        "new_entry_gap": (1, 10_000), "max_tracked": (1, 5_000), "warmup_factor": (0.0, 50.0),
        "absorb_ratio": (0.5, 1.0), "half_life_days": (0.5, 365.0), "burst_ratio": (2.0, 50.0),
        "burst_min": (1.0, 50.0), "min_evidence": (0, 50),
    }

    def validate(self) -> "EngineConfig":
        for name, (lo, hi) in self._RANGES.items():
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v):
                raise ValueError(f"{name} must be a number")
            if not lo <= v <= hi:
                raise ValueError(f"{name} must be in [{lo}, {hi}] (got {v})")
        if self.max_tracked < self.k:
            raise ValueError(f"max_tracked ({self.max_tracked}) must be >= k ({self.k})")
        if self.decay_unit not in ("article", "day"):
            raise ValueError("decay_unit must be 'article' or 'day'")
        if not isinstance(self.phrases, bool):
            raise ValueError("phrases must be true or false")
        return self

    @property
    def decay_c(self) -> float:
        """Decay constant per step (article or day)."""
        if self.decay_unit == "day":
            return 1.0 - 0.5 ** (1.0 / self.half_life_days)
        return self.c

    @property
    def half_life_steps(self) -> float:
        return math.log(0.5) / math.log(1.0 - self.decay_c)

    @property
    def short_c(self) -> float:
        """Burst detector: same unit, half-life divided by burst_ratio."""
        return 1.0 - 0.5 ** (1.0 / (self.half_life_steps / self.burst_ratio))


@dataclass
class Tracker:
    start_t: int            # first article index counted
    dgim: DGIM
    exact: ExactWindowCount
    pinned: bool            # True: added by the user, never evicted
    last_active: int        # last snapshot in which the keyword was in Top K (or created)
    generation: int         # 1 = first tracking, 2+ = re-tracked after eviction


class TrendEngine:
    def __init__(self, articles: list[Article], config: EngineConfig | None = None):
        self.articles = articles
        # calendar day number of every article (for per-day decay)
        self._day = [_date.fromisoformat(a.date).toordinal() for a in articles]
        # articles per calendar month (dashboard timeline density; computed once)
        months: Counter = Counter(a.date[:7] for a in articles)
        self._months = [{"month": m, "count": n} for m, n in sorted(months.items())]
        self.reset(config or EngineConfig())

    # ------------------------------------------------------------------ setup
    def reset(self, config: EngineConfig | None = None, start_date: str | None = None) -> None:
        if config is not None:
            self.config = config.validate()
        cfg = self.config
        dc = cfg.decay_c
        self.pruned = DecayingWindow(dc, cfg.threshold, track_error=True)
        self.baseline = DecayingWindow(dc, None)
        self._baseline_cache: tuple = (None, [])
        self.short = DecayingWindow(cfg.short_c, cfg.threshold)     # burst detector
        self._last_day: int | None = None
        self.postings: dict[str, list[int]] = {}
        self.trackers: dict[str, Tracker] = {}
        self.track_generations: Counter = Counter()
        self.evictions = 0
        self.track_refusals = 0
        self.history: dict[str, deque] = {}
        # (t, date, [(keyword, score), ...] top K) per snapshot -> rank-race chart (v0.4)
        self.rank_snaps: deque = deque(maxlen=160)
        self.events: deque = deque(maxlen=60)
        self.first_entry: dict[str, dict] = {}
        self._last_in_topk: dict[str, int] = {}
        self._topk_now: set[str] = set()
        self._snap = 0
        self._prev_display: dict[str, int] = {}
        self._display: dict[str, int] = {}
        self.pos = 0
        self.start_pos = 0
        self._proc_seconds = 0.0
        self._proc_docs = 0

        start = first_index_on_or_after(self.articles, start_date) if start_date else 0
        start = min(start, len(self.articles) - 1)
        if cfg.decay_unit == "day":
            first_day = self._day[start] - int(cfg.warmup_factor / dc)
            warm = start
            while warm > 0 and self._day[warm - 1] >= first_day:
                warm -= 1
        else:
            warm = max(0, start - int(cfg.warmup_factor / dc))
        self.pos = warm
        while self.pos < start:
            self._ingest(self.articles[self.pos], warmup=True)
        self.start_pos = start
        self._take_snapshot(emit_events=False)
        self._roll_display()
        self._prev_display = dict(self._display)

    # ------------------------------------------------------------------ stream
    def _ingest(self, art: Article, warmup: bool = False) -> None:
        cfg = self.config
        toks = art.tokens + art.phrases if cfg.phrases else art.tokens
        t = art.idx
        if cfg.decay_unit == "day":
            day = self._day[t]
            steps = 0 if self._last_day is None else day - self._last_day
            self._last_day = day
        else:
            steps = 1
        self.pruned.add(toks, steps)
        self.baseline.add(toks, steps)
        self.short.add(toks, steps)
        postings = self.postings
        for w in toks:
            lst = postings.get(w)
            if lst is None:
                postings[w] = [t]
            else:
                lst.append(t)
            tr = self.trackers.get(w)
            if tr is not None:
                tr.dgim.add(t)
                tr.exact.add(t)
        self.pos += 1
        if not warmup and (self.pos - self.start_pos) % self.config.snapshot_every == 0:
            self._take_snapshot(emit_events=True)

    def step(self, n: int) -> int:
        """Advance the stream by up to ``n`` articles. Returns how many were read."""
        n = max(0, min(n, len(self.articles) - self.pos))
        t0 = time.perf_counter()
        for _ in range(n):
            self._ingest(self.articles[self.pos])
        self._proc_seconds += time.perf_counter() - t0
        self._proc_docs += n
        self._roll_display()
        return n

    @property
    def now(self) -> int:
        """Index of the most recently processed article (the stream clock)."""
        return self.pos - 1

    @property
    def finished(self) -> bool:
        return self.pos >= len(self.articles)

    # ------------------------------------------------------------------ snapshots
    def _ranked(self, window: DecayingWindow, k: int) -> list[tuple[str, float]]:
        """Displayed ranking: Top k after phrase absorption (if phrases are on)."""
        if window is self.baseline and k == self.config.k:
            # The exact window holds every keyword, so its Top K costs ~10 ms; state()
            # needs it three times per tick (ranking, metrics, comparison view).
            key = (self.pos, self.config.k)
            if self._baseline_cache[0] != key:
                self._baseline_cache = (key, self._ranked_uncached(window, k))
            return self._baseline_cache[1]
        return self._ranked_uncached(window, k)

    def _ranked_uncached(self, window: DecayingWindow, k: int) -> list[tuple[str, float]]:
        if not self.config.phrases:
            return window.top_k(k)
        return absorb(window.top_k(3 * k), k, self.config.absorb_ratio)

    def _take_snapshot(self, emit_events: bool) -> None:
        cfg = self.config
        self._snap += 1
        top = self._ranked(self.pruned, 2 * cfg.k)
        t = self.now
        for rank, (w, s) in enumerate(top, 1):
            h = self.history.get(w)
            if h is None:
                h = self.history[w] = deque(maxlen=240)
            h.append((t, round(s, 3), rank))
        for w in self.trackers:
            h = self.history.get(w)
            if h is None:
                h = self.history[w] = deque(maxlen=240)
            if not h or h[-1][0] != t:
                h.append((t, round(self.pruned.score(w), 3), None))

        self._topk_now = {w for w, _ in top[: cfg.k]}
        self.rank_snaps.append((t, self.articles[t].date if t >= 0 else "",
                                [(w, round(s, 2)) for w, s in top[: cfg.k]]))
        for rank, (w, s) in enumerate(top[: cfg.k], 1):
            last = self._last_in_topk.get(w)
            kind = "first" if last is None else ("reentry" if self._snap - last > cfg.new_entry_gap else None)
            if emit_events and kind:
                ev = {"t": t, "date": self.articles[t].date if t >= 0 else "",
                      "keyword": w, "rank": rank, "score": round(s, 2), "kind": kind}
                self.events.appendleft(ev)
                self.first_entry.setdefault(w, ev)
            self._last_in_topk[w] = self._snap
            tr = self.trackers.get(w)
            if tr is not None:
                tr.last_active = self._snap
            else:
                self.track(w, pinned=False)

    def _roll_display(self) -> None:
        self._prev_display = self._display
        self._display = {w: r for r, (w, _) in enumerate(self._ranked(self.pruned, self.config.k), 1)}

    # ------------------------------------------------------------------ DGIM tracking
    def track(self, w: str, pinned: bool = True) -> tuple[bool, str]:
        """Start (or pin) DGIM tracking of ``w``. Returns (ok, message)."""
        w = w.lower()
        cfg = self.config
        tr = self.trackers.get(w)
        if tr is not None:
            if pinned and not tr.pinned:
                if self._pinned_count() >= cfg.max_tracked // 2:
                    return False, "고정 가능한 키워드 수를 초과했습니다."
                tr.pinned = True
                return True, "이미 추적 중인 키워드를 고정했습니다."
            return True, "이미 추적 중입니다."
        if pinned and self._pinned_count() >= cfg.max_tracked // 2:
            self.track_refusals += 1
            return False, f"사용자 고정은 최대 {cfg.max_tracked // 2}개까지 가능합니다."
        if len(self.trackers) >= cfg.max_tracked and not self._evict_one():
            self.track_refusals += 1
            return False, "추적 용량이 가득 찼고 교체할 수 있는 자동 추적 키워드가 없습니다."
        self.track_generations[w] += 1
        self.trackers[w] = Tracker(start_t=self.pos, dgim=DGIM(cfg.dgim_n, cfg.dgim_r),
                                   exact=ExactWindowCount(cfg.dgim_n), pinned=pinned,
                                   last_active=self._snap, generation=self.track_generations[w])
        return True, "추적을 시작했습니다." if self.track_generations[w] == 1 else "추적을 다시 시작했습니다(관측 초기화)."

    def _pinned_count(self) -> int:
        return sum(1 for tr in self.trackers.values() if tr.pinned)

    def _evict_one(self) -> bool:
        victims = [(tr.last_active, w) for w, tr in self.trackers.items()
                   if not tr.pinned and w not in self._topk_now]
        if not victims:
            return False
        _, w = min(victims)
        del self.trackers[w]
        self.evictions += 1
        return True

    def expire_all(self) -> None:
        """Drop expired buckets/positions of every tracker at the same clock time."""
        t = self.now
        for tr in self.trackers.values():
            tr.dgim.expire(t)
            tr.exact.expire(t)

    def dgim_info(self, w: str) -> dict | None:
        tr = self.trackers.get(w)
        if tr is None:
            return None
        t = self.now
        est = tr.dgim.estimate(t) if t >= tr.start_t else 0.0
        exact = tr.exact.count(t) if t >= tr.start_t else 0
        observed = max(0, min(self.config.dgim_n, t - tr.start_t + 1))
        return {
            "estimate": round(est, 1), "exact": exact,
            "abs_error": round(abs(est - exact), 1),
            "rel_error": round(abs(est - exact) / exact, 4) if exact else 0.0,
            "window": self.config.dgim_n, "observed": observed,
            "partial": observed < self.config.dgim_n,
            "since_t": tr.start_t,
            "since_date": self.articles[min(tr.start_t, len(self.articles) - 1)].date,
            "buckets": tr.dgim.num_buckets, "dgim_bits": tr.dgim.memory_bits(),
            "exact_bits": tr.exact.memory_bits(), "bound": tr.dgim.error_bound(),
            "pinned": tr.pinned, "generation": tr.generation,
        }

    # ------------------------------------------------------------------ queries
    def ranking(self) -> list[dict]:
        cfg = self.config
        top = self._ranked(self.pruned, cfg.k)
        errs = [self.pruned.key_error(w) for w, _ in top]
        # Raw candidates (absorbed words included) -> conservative for the last row.
        outside, complete = outside_candidates(self.pruned, top)
        flags = uncertainty(top, errs, outside, self.pruned.error_bound(), complete)
        base_top = self._ranked(self.baseline, cfg.k)
        base_rank = {w: r for r, (w, _) in enumerate(base_top, 1)}
        lo1, _, _, _ = self._recent_windows()
        now = self.now
        rows = []
        for (rank, (w, s)), fl, err in zip(enumerate(top, 1), flags, errs):
            prev = self._prev_display.get(w)
            dg = self.dgim_info(w)
            evidence = self.mentions_between(w, lo1, now)
            rows.append({
                "rank": rank, "keyword": w, "score": round(s, 2),
                "prev_rank": prev, "delta": (prev - rank) if prev else None,
                "baseline_rank": base_rank.get(w),
                "baseline_score": round(self.baseline.score(w), 2),
                # v0.4: articles in the recent window (1/c days or articles) behind this row
                "evidence": evidence, "thin": evidence <= cfg.min_evidence,
                "dgim": {"estimate": dg["estimate"], "exact": dg["exact"],
                         "observed": dg["observed"], "partial": dg["partial"]} if dg else None,
                "spark": [p[1] for p in list(self.history.get(w, ()))[-30:]],
                "may_swap_above": fl["swap_above"], "may_swap_below": fl["swap_below"],
                "error_bound": round(err, 3),
            })
        return rows

    def burst(self, k: int = 10) -> list[dict]:
        """Keywords mentioned more than their long-term rate predicts (see ranking.burst_scores)."""
        rows = burst_scores(self.short, self.pruned, self.config.burst_min, 3 * k)
        if self.config.phrases:
            kept = absorb([(r["keyword"], r["excess"]) for r in rows], k, self.config.absorb_ratio)
            keep = {w for w, _ in kept}
            rows = [r for r in rows if r["keyword"] in keep]
        rows = rows[:k]
        lo1, _, _, _ = self._recent_windows()
        display = self._display
        for r in rows:
            r["evidence"] = self.mentions_between(r["keyword"], lo1, self.now)
            r["rank"] = display.get(r["keyword"])          # None = not in the Top K right now
        return rows

    def baseline_ranking(self) -> list[dict]:
        """Top K of the exact computation (every keyword kept), for the comparison view."""
        shown = self._display
        return [{"rank": r, "keyword": w, "score": round(s, 2), "pruned_rank": shown.get(w)}
                for r, (w, s) in enumerate(self._ranked(self.baseline, self.config.k), 1)]

    def rank_history(self, k: int = 10, n: int = 60) -> dict:
        """Last ``n`` ranking snapshots (plus the current ranking) as rank series for
        the keywords that were in the Top ``k`` of any of them: the rank-race chart.

        Output: {"t": [...], "dates": [...], "series": {keyword: [rank | None, ...]},
                 "k": k}. A None means the keyword was outside the Top k at that time.
        """
        k = max(1, min(k, self.config.k))
        snaps = list(self.rank_snaps)[-n:]
        now_top = [(w, 0.0) for w, _ in sorted(self._display.items(), key=lambda x: x[1])][:k]
        t_now = self.now
        if not snaps or snaps[-1][0] != t_now:
            snaps.append((t_now, self.articles[max(t_now, 0)].date, now_top))
        words: dict[str, None] = {}
        for _, _, top in snaps:
            for w, _ in top[:k]:
                words.setdefault(w, None)
        series = {w: [None] * len(snaps) for w in words}
        for i, (_, _, top) in enumerate(snaps):
            for r, (w, _) in enumerate(top[:k], 1):
                series[w][i] = r
        return {"t": [s[0] for s in snaps], "dates": [s[1] for s in snaps], "series": series, "k": k}

    def timeline(self) -> dict:
        """Static data for the dashboard timeline: article density per month and
        the case-study events (markers)."""
        from .cases import events_as_dicts
        return {"months": self._months, "events": events_as_dicts(),
                "first_date": self.articles[0].date, "last_date": self.articles[-1].date,
                "total": len(self.articles)}

    SEEK_FORWARD_MAX = 40_000   # beyond this many articles a forward seek restarts instead

    def seek(self, date: str) -> str:
        """Move the stream to ``date``. Forward and near: step through the articles
        (Top K entry events stay intact). Backward or far: restart at that date with
        the current settings (warm-up included). Returns "stepped" or "reset"."""
        target = first_index_on_or_after(self.articles, date)
        target = max(0, min(target, len(self.articles) - 1))
        if target > self.now and target - self.pos <= self.SEEK_FORWARD_MAX:
            self.step(target - self.pos + 1)
            return "stepped"
        self.reset(start_date=date)
        return "reset"

    def metrics(self) -> dict:
        cfg = self.config
        k = cfg.k
        top = [w for w, _ in self._ranked(self.pruned, k)]
        base = [w for w, _ in self._ranked(self.baseline, k)]
        overlap = len(set(top) & set(base))
        max_err = max((self.baseline.score(w) - self.pruned.score(w) for w in set(top) | set(base)),
                      default=0.0)
        self.expire_all()   # same reference time for every tracker before measuring
        dgim_bits = sum(tr.dgim.memory_bits() for tr in self.trackers.values())
        exact_bits = sum(tr.exact.memory_bits() for tr in self.trackers.values())
        postings = sum(len(v) for v in self.postings.values())
        n_pruned, n_base = len(self.pruned), len(self.baseline)
        return {
            "tracked_pruned": n_pruned, "tracked_baseline": n_base,
            "peak_pruned": self.pruned.peak_size,
            "keyword_reduction_pct": round(100 * (1 - n_pruned / n_base), 1) if n_base else 0.0,
            "heap_entries": self.pruned.heap_size,
            "topk_overlap": overlap, "k": k,
            "same_order": top == base,
            "max_score_error_topk": round(max_err, 4),
            "score_error_bound": round(self.pruned.error_bound(), 4),
            "dropped_total": self.pruned.dropped_total,
            "short_window_keywords": len(self.short),
            "dgim_tracked": len(self.trackers), "dgim_capacity": cfg.max_tracked,
            "dgim_pinned": self._pinned_count(), "dgim_evictions": self.evictions,
            "dgim_bits": dgim_bits, "exact_window_bits": exact_bits,
            "index_postings": postings,
            "index_bytes_est": postings * 4 + len(self.postings) * 16,
            "docs_per_sec": round(self._proc_docs / self._proc_seconds) if self._proc_seconds else None,
        }

    def clock(self) -> dict:
        t = max(self.now, 0)
        cfg = self.config
        c = cfg.decay_c
        return {
            "decay_unit": cfg.decay_unit, "unit_label": "일" if cfg.decay_unit == "day" else "건",
            "t": self.now, "date": self.articles[t].date,
            "start_date": self.articles[self.start_pos].date,
            "end_date": self.articles[-1].date,
            "processed": self.pos - self.start_pos,
            "total": len(self.articles),
            "progress": round(self.pos / len(self.articles), 4),
            "finished": self.finished,
            "effective_window": round(1 / c),
            "half_life": round(cfg.half_life_steps, 1) if cfg.decay_unit == "day" else round(cfg.half_life_steps),
            # share of the total weight carried by the latest 1/c articles (long stream)
            "recent_weight_share": round(1 - (1 - c) ** round(1 / c), 3),
            # calendar days covered by the last 1/c articles (density varies by year)
            "window_span_days": (self._span_days(round(1 / c)) if cfg.decay_unit == "article"
                                 else round(1 / c)),
            "articles_in_window": (self._articles_in_last_days(round(1 / c)) if cfg.decay_unit == "day"
                                   else round(1 / c)),
        }

    def _articles_in_last_days(self, days: int) -> int:
        t = max(self.now, 0)
        lo = self._day[t] - days + 1
        i = t
        while i >= 0 and self._day[i] >= lo:
            i -= 1
        return t - i

    def _span_days(self, n: int) -> int:
        t = max(self.now, 0)
        a, b = self.articles[max(0, t - n + 1)].date, self.articles[t].date
        return (_date.fromisoformat(b) - _date.fromisoformat(a)).days + 1

    def state(self) -> dict:
        return {
            "clock": self.clock(), "config": asdict(self.config),
            "ranking": self.ranking(), "events": list(self.events)[:25],
            "burst": self.burst(10), "metrics": self.metrics(),
            # v0.4: comparison view + rank-race chart
            "baseline_ranking": self.baseline_ranking(),
            "race": self.rank_history(k=10, n=60),
        }

    # ------------------------------------------------------------------ article lookups
    def _postings_upto(self, w: str, as_of: int | None) -> list[int]:
        lst = self.postings.get(w.lower(), [])
        if as_of is None:
            return lst
        return lst[: bisect_right(lst, as_of)]

    def related(self, w: str, limit: int = 10, offset: int = 0, as_of: int | None = None) -> list[dict]:
        """Most recent articles (up to ``as_of``) whose headline contains ``w``."""
        lst = self._postings_upto(w, as_of)
        ref = self.now if as_of is None else as_of
        out = []
        for idx in reversed(lst[: max(0, len(lst) - offset)]):
            a = self.articles[idx]
            out.append({"id": a.idx, "date": a.date, "headline": a.headline,
                        "description": a.description, "category": a.category,
                        "link": a.link, "age": ref - a.idx})
            if len(out) >= limit:
                break
        return out

    def article(self, idx: int) -> Article | None:
        return self.articles[idx] if 0 <= idx <= self.now else None

    def cooccurring(self, w: str, recent: int = 60, top: int = 10, as_of: int | None = None) -> list[tuple[str, int]]:
        w = w.lower()
        cnt: Counter = Counter()
        for idx in self._postings_upto(w, as_of)[-recent:]:
            cnt.update(x for x in self.articles[idx].tokens if x != w)
        return cnt.most_common(top)

    def categories(self, w: str, recent: int = 60, as_of: int | None = None) -> list[tuple[str, int]]:
        cnt = Counter(self.articles[i].category for i in self._postings_upto(w, as_of)[-recent:])
        return cnt.most_common(5)

    def mentions_between(self, w: str, lo: int, hi: int) -> int:
        """Number of articles with index in (lo, hi] containing ``w``."""
        lst = self.postings.get(w.lower(), [])
        return bisect_right(lst, hi) - bisect_right(lst, lo)

    def _recent_windows(self) -> tuple[int, int, str, str]:
        """Article-index bounds of "recent" and "previous" windows in the active decay
        unit (1/c articles, or 1/c days), for mentions_between(lo, hi]."""
        cfg = self.config
        now = self.now
        span = round(1 / cfg.decay_c)
        if cfg.decay_unit == "day":
            today = self._day[max(now, 0)]
            lo1 = bisect_left(self._day, today - span + 1) - 1
            lo2 = bisect_left(self._day, today - 2 * span + 1) - 1
            return lo1, lo2, f"최근 {span}일", f"그 이전 {span}일"
        return now - span, now - 2 * span, f"최근 {span:,}건", f"그 이전 {span:,}건"

    def keyword_detail(self, w: str) -> dict:
        w = w.lower()
        score = self.pruned.score(w)
        # Rank in the *displayed* ranking (after phrase absorption) when it is within
        # the first 200; otherwise among all kept keywords. None if pruned away.
        shown = [x for x, _ in self._ranked(self.pruned, 200)]
        if w in shown:
            rank = shown.index(w) + 1
        elif w in self.pruned:
            rank = 1 + sum(1 for _, s in self.pruned.items() if s > score)
        else:
            rank = None
        now = self.now
        lo1, lo2, label, prev_label = self._recent_windows()
        first_seen = self.postings.get(w, [None])[0]
        return {
            "keyword": w, "rank": rank, "in_top_k": rank is not None and rank <= self.config.k,
            "score": round(score, 3),
            "baseline_score": round(self.baseline.score(w), 3),
            "in_pruned": w in self.pruned,
            "total_mentions_so_far": len(self.postings.get(w, [])),
            "mentions_in_effective_window": self.mentions_between(w, lo1, now),
            "mentions_in_previous_window": self.mentions_between(w, lo2, lo1),
            "window_label": label, "previous_window_label": prev_label,
            "first_seen": ({"t": first_seen, "date": self.articles[first_seen].date}
                           if first_seen is not None else None),
            "first_entry": self.first_entry.get(w),
            "history": [{"t": t, "date": self.articles[t].date if t >= 0 else "", "score": s, "rank": r}
                        for t, s, r in self.history.get(w, ())],
            "dgim": self.dgim_info(w),
            "cooccurring": self.cooccurring(w),
            "categories": self.categories(w),
            "related": self.related(w, 12),
            "events": [e for e in self.events if e["keyword"] == w],
        }
