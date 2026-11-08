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
    warmup_factor: float = 10.0  # warm-up of warmup_factor / c articles before start

    # (min, max) inclusive ranges; checked before the engine state is touched
    _RANGES = {
        "c": (1e-5, 0.5), "threshold": (0.01, 0.99), "k": (1, 100),
        "dgim_n": (10, 100_000), "dgim_r": (2, 64), "snapshot_every": (1, 10_000),
        "new_entry_gap": (1, 10_000), "max_tracked": (1, 5_000), "warmup_factor": (0.0, 50.0),
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
        return self


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
        self.reset(config or EngineConfig())

    # ------------------------------------------------------------------ setup
    def reset(self, config: EngineConfig | None = None, start_date: str | None = None) -> None:
        if config is not None:
            self.config = config.validate()
        cfg = self.config
        self.pruned = DecayingWindow(cfg.c, cfg.threshold)
        self.baseline = DecayingWindow(cfg.c, None)
        self.postings: dict[str, list[int]] = {}
        self.trackers: dict[str, Tracker] = {}
        self.track_generations: Counter = Counter()
        self.evictions = 0
        self.track_refusals = 0
        self.history: dict[str, deque] = {}
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
        warm = max(0, start - int(cfg.warmup_factor / cfg.c))
        self.pos = warm
        while self.pos < start:
            self._ingest(self.articles[self.pos], warmup=True)
        self.start_pos = start
        self._take_snapshot(emit_events=False)
        self._roll_display()
        self._prev_display = dict(self._display)

    # ------------------------------------------------------------------ stream
    def _ingest(self, art: Article, warmup: bool = False) -> None:
        toks = art.tokens
        self.pruned.add(toks)
        self.baseline.add(toks)
        t = art.idx
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
    def _take_snapshot(self, emit_events: bool) -> None:
        cfg = self.config
        self._snap += 1
        top = self.pruned.top_k(2 * cfg.k)
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
        self._display = {w: r for r, (w, _) in enumerate(self.pruned.top_k(self.config.k), 1)}

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
        top = self.pruned.top_k(cfg.k)
        base_top = self.baseline.top_k(cfg.k)
        base_rank = {w: r for r, (w, _) in enumerate(base_top, 1)}
        rows = []
        for rank, (w, s) in enumerate(top, 1):
            prev = self._prev_display.get(w)
            dg = self.dgim_info(w)
            rows.append({
                "rank": rank, "keyword": w, "score": round(s, 2),
                "prev_rank": prev, "delta": (prev - rank) if prev else None,
                "baseline_rank": base_rank.get(w),
                "baseline_score": round(self.baseline.score(w), 2),
                "dgim": {"estimate": dg["estimate"], "exact": dg["exact"],
                         "observed": dg["observed"], "partial": dg["partial"]} if dg else None,
                "spark": [p[1] for p in list(self.history.get(w, ()))[-30:]],
            })
        return rows

    def metrics(self) -> dict:
        cfg = self.config
        k = cfg.k
        top = [w for w, _ in self.pruned.top_k(k)]
        base = [w for w, _ in self.baseline.top_k(k)]
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
            "dgim_tracked": len(self.trackers), "dgim_capacity": cfg.max_tracked,
            "dgim_pinned": self._pinned_count(), "dgim_evictions": self.evictions,
            "dgim_bits": dgim_bits, "exact_window_bits": exact_bits,
            "index_postings": postings,
            "index_bytes_est": postings * 4 + len(self.postings) * 16,
            "docs_per_sec": round(self._proc_docs / self._proc_seconds) if self._proc_seconds else None,
        }

    def clock(self) -> dict:
        t = max(self.now, 0)
        c = self.config.c
        return {
            "t": self.now, "date": self.articles[t].date,
            "start_date": self.articles[self.start_pos].date,
            "end_date": self.articles[-1].date,
            "processed": self.pos - self.start_pos,
            "total": len(self.articles),
            "progress": round(self.pos / len(self.articles), 4),
            "finished": self.finished,
            "effective_window": round(1 / c),
            "half_life": round(math.log(0.5) / math.log(1 - c)),
            # share of the total weight carried by the latest 1/c articles (long stream)
            "recent_weight_share": round(1 - (1 - c) ** round(1 / c), 3),
            # calendar days covered by the last 1/c articles (density varies by year)
            "window_span_days": self._span_days(round(1 / c)),
        }

    def _span_days(self, n: int) -> int:
        t = max(self.now, 0)
        a, b = self.articles[max(0, t - n + 1)].date, self.articles[t].date
        return (_date.fromisoformat(b) - _date.fromisoformat(a)).days + 1

    def state(self) -> dict:
        return {
            "clock": self.clock(), "config": asdict(self.config),
            "ranking": self.ranking(), "events": list(self.events)[:25],
            "metrics": self.metrics(),
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

    def keyword_detail(self, w: str) -> dict:
        w = w.lower()
        score = self.pruned.score(w)
        # Full rank among all kept keywords (not just Top K); None if pruned away.
        rank = (1 + sum(1 for _, s in self.pruned.items() if s > score)) if w in self.pruned else None
        win = round(1 / self.config.c)
        now = self.now
        first_seen = self.postings.get(w, [None])[0]
        return {
            "keyword": w, "rank": rank, "in_top_k": rank is not None and rank <= self.config.k,
            "score": round(score, 3),
            "baseline_score": round(self.baseline.score(w), 3),
            "in_pruned": w in self.pruned,
            "total_mentions_so_far": len(self.postings.get(w, [])),
            "mentions_in_effective_window": self.mentions_between(w, now - win, now),
            "mentions_in_previous_window": self.mentions_between(w, now - 2 * win, now - win),
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
