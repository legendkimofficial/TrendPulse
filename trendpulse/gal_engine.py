"""GDELT GAL replay engine: 7-day decaying topic ranking and 72-hour surges.

Replay contract
---------------
* The clock is the batch observation time (``obs``, epoch seconds). Articles of
  one observation time are ingested together; the snapshot is published only
  after all of them are counted.
* Ranking at time ``t`` considers articles with ``t-7d < obs <= t`` (exactly
  ``t-7d`` is EXPIRED, ``t`` is INCLUDED). Each article contributes
  ``2 ** (-(t - obs) / half_life)`` (default half-life 48h) once per keyword it
  contains (document frequency). Scores fall as the clock advances even when no
  new article arrives (``advance_clock`` re-syncs at a later time).
* Tie-break: score descending, then keyword ascending (fixed, documented; the
  HuffPost engine's decaying.py uses keyword-descending — a different, stated
  convention).
* Pruning (experimental variant, ``threshold`` set): at each snapshot, after
  syncing scores at ``t``, any keyword whose score is below ``threshold`` is
  dropped *with all of its state*. If it is mentioned again it starts from 1;
  contributions lost at the drop are never re-subtracted later (fresh state),
  so the pruned score can only under-estimate the exact one. The exact variant
  (``threshold=None``, used by the dashboard) keeps every contribution that is
  still inside the 7-day window.

72-hour surge
-------------
* recent  = (t-72h, t], previous = (t-144h, t-72h]; both use the same analyzed
  article population (normalized English articles) as the denominator.
* surge(keyword) = 100 * (recent_share - previous_share) in percentage points,
  where share = keyword articles / interval articles. A zero previous count is
  fine as long as the previous interval has coverage and enough articles.
* A row is only shown when: both intervals' observability is confirmed (data
  reaches t-144h and no known collection gap overlaps either interval), both
  denominators >= ``min_articles_per_interval``, the keyword has
  >= ``min_keyword_articles`` recent articles from >= ``min_keyword_outlets``
  distinct outlets. Otherwise the surge is deferred with the reason
  (``observability`` / ``collection_gap`` / ``denominator`` / ``keyword_support``).

Entries: a keyword in the current Top K that was not in the previous snapshot's
Top K is a new entry; ``first`` if it has never been in any earlier Top K,
``re-entry`` otherwise. The very first snapshot of a replay has no previous
snapshot; all of its rows are marked ``initial``.

DGIM tracking reuses ``trendpulse.dgim.DGIM`` (approximate) and
``trendpulse.sliding.ExactWindowCount`` (exact positions-queue, the reference
implementation), both fed with the same article indices from the same tracking
start point; ``dgim_info`` reports both plus the actually observed range.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
from array import array
from bisect import bisect_right, bisect_left
from dataclasses import asdict, dataclass, field

from .dgim import DGIM
from .gal import GalArticle
from .sliding import ExactWindowCount


@dataclass
class GalConfig:
    k: int = 20
    half_life_h: float = 48.0            # ranking decay half-life (configurable)
    window_h: float = 168.0              # 7-day ranking window
    burst_h: float = 72.0                # surge interval
    min_articles_per_interval: int = 200  # denominator minimum, both intervals
    min_keyword_articles: int = 5        # keyword articles in the recent interval
    min_keyword_outlets: int = 3         # distinct outlets in the recent interval
    threshold: float | None = None       # pruning threshold (experimental variant)
    dgim_n: int = 1000
    dgim_r: int = 2
    max_trackers: int = 200
    gaps: list = field(default_factory=list)  # collection gaps [{"start","end"} ISO]


class _Kw:
    """Per-keyword state: mention list + pointers + incrementally decayed sum.

    ``ms`` holds mention epoch seconds of the current 7-day window (append-only
    list with a moving ``head``); ``S`` is the window score anchored at
    ``last_ts``. ``p72``/``p144`` are the first indices inside (t-72h, t] and
    (t-144h, t-72h] respectively, so interval counts are O(1) after a sync.
    """
    __slots__ = ("ms", "head", "p72", "p144", "S", "last_ts")

    def __init__(self, obs: int):
        self.ms = [obs]
        self.head = 0
        self.p72 = 0
        self.p144 = 0
        self.S = 1.0
        self.last_ts = obs

    def compact(self) -> None:
        if self.head > 64 and self.head * 2 > len(self.ms):
            del self.ms[:self.head]
            self.p72 -= self.head
            self.p144 -= self.head
            self.head = 0


@dataclass
class Tracker:
    start_i: int
    dgim: DGIM
    exact: ExactWindowCount
    pinned: bool
    last_active: int


def _iso(ts: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GalEngine:
    def __init__(self, articles: list[GalArticle], config: GalConfig | None = None):
        self.articles = articles
        self.cfg = config or GalConfig()
        self.obs = [a.obs for a in articles]
        hl = self.cfg.half_life_h * 3600.0
        self._hl = hl
        self._win = self.cfg.window_h * 3600.0
        self._b = self.cfg.burst_h * 3600.0
        self._gaps_ts = [(g.get("start_ts"), g.get("end_ts")) for g in
                         (self.cfg.gaps or []) if g.get("start_ts") is not None]
        self.reset()

    # ------------------------------------------------------------------ setup
    def reset(self) -> None:
        self.kw: dict[str, _Kw] = {}
        self.postings: dict[str, array] = {}
        self.trackers: dict[str, Tracker] = {}
        self.pos = 0                     # next article index to ingest
        self.now_t: int | None = None    # current clock (epoch s) once anything ingested
        self.snapshots: list[dict] = []  # published snapshots (compact)
        self.prev_top: set[str] = set()
        self.seen_top: set[str] = set()
        self.snap_i = 0
        self.dropped_events = 0
        self.peak_kw = 0

    # ------------------------------------------------------------------ decay math
    def _decay(self, dt: float) -> float:
        return 2.0 ** (-dt / self._hl)

    def _add_kw(self, w: str, obs: int) -> None:
        st = self.kw.get(w)
        if st is None:
            st = self.kw[w] = _Kw(obs)
            p = self.postings.get(w)
            if p is None:
                self.postings[w] = array("i")
        else:
            if obs != st.last_ts:
                st.S *= self._decay(obs - st.last_ts)
                st.last_ts = obs
            st.S += 1.0
            st.ms.append(obs)

    def _sync(self, st: _Kw, t: int) -> None:
        """Expire the 7-day window, move interval pointers, anchor S at t."""
        if t != st.last_ts:
            st.S *= self._decay(t - st.last_ts)
            st.last_ts = t
        limit = t - self._win
        ms, head = st.ms, st.head
        while head < len(ms) and ms[head] <= limit:
            st.S -= self._decay(t - ms[head])
            head += 1
        st.head = head
        p = st.p72
        lim72 = t - self._b
        while p < len(ms) and ms[p] <= lim72:
            p += 1
        st.p72 = p
        q = st.p144
        lim144 = t - 2 * self._b
        while q < len(ms) and ms[q] <= lim144:
            q += 1
        st.p144 = q
        st.compact()

    def score_at(self, w: str, t: int) -> float:
        """Exact score of ``w`` at ``t`` — direct re-summation from the mention
        list (used to cross-check the incremental ``S``; O(mentions))."""
        st = self.kw.get(w)
        if st is None:
            return 0.0
        limit = t - self._win
        return sum(self._decay(t - ts) for ts in st.ms[st.head:] if ts > limit)

    # ------------------------------------------------------------------ ingest
    def _ingest_group(self) -> int:
        """Ingest all articles sharing the next observation time; snapshot."""
        t = self.obs[self.pos]
        while self.pos < len(self.articles) and self.obs[self.pos] == t:
            art = self.articles[self.pos]
            i = art.i
            for w in art.tokens:
                self._add_kw(w, t)
                self.postings[w].append(i)
                tr = self.trackers.get(w)
                if tr is not None:
                    tr.dgim.add(i)
                    tr.exact.add(i)
            self.pos += 1
        self.now_t = t
        self._take_snapshot()
        return t

    def step(self, batches: int = 1) -> int:
        """Advance by up to ``batch`` observation groups. Returns groups done."""
        done = 0
        while done < batches and self.pos < len(self.articles):
            self._ingest_group()
            done += 1
        return done

    def advance_clock(self, t_iso: str) -> dict:
        """Move the clock forward without new articles: decay + 7d expiry apply."""
        from .gal import parse_iso
        t = parse_iso(t_iso) if isinstance(t_iso, str) else int(t_iso)
        if self.now_t is None or t < self.now_t:
            raise ValueError("clock can only move forward")
        self.now_t = t
        self._take_snapshot()
        return self.state()

    @property
    def finished(self) -> bool:
        return self.pos >= len(self.articles)

    # ------------------------------------------------------------------ burst helpers
    def _interval_count(self, lo: int, hi: int) -> int:
        return bisect_right(self.obs, hi) - bisect_right(self.obs, lo)

    def _coverage_ok(self, t: int) -> tuple[bool, str | None]:
        need = t - 2 * self._b
        if not self.obs or self.obs[0] > need:
            return False, "observability"          # data does not reach t-144h
        for gs, ge in self._gaps_ts:
            if gs < t and ge > t - 2 * self._b:
                return False, "collection_gap"
        return True, None

    def _kw_interval_articles(self, w: str, lo: int, hi: int) -> list[int]:
        """Article indices mentioning ``w`` with obs in (lo, hi]."""
        posts = self.postings.get(w)
        if not posts:
            return []
        # postings are in article order == obs order; binary search by obs
        obs = self.obs
        # find first posting index with obs > lo
        a, b = 0, len(posts)
        while a < b:
            m = (a + b) // 2
            if obs[posts[m]] <= lo:
                a = m + 1
            else:
                b = m
        lo_i = a
        a, b = lo_i, len(posts)
        while a < b:
            m = (a + b) // 2
            if obs[posts[m]] <= hi:
                a = m + 1
            else:
                b = m
        return [posts[j] for j in range(lo_i, a)]

    # ------------------------------------------------------------------ snapshot
    def _take_snapshot(self) -> dict:
        cfg = self.cfg
        t = self.now_t
        th = cfg.threshold
        for st in self.kw.values():
            self._sync(st, t)
        # exact-window expiry: a keyword with no mention left inside the 7-day
        # window leaves the state table entirely (this is not pruning; the exact
        # and pruned variants both do it, and it keeps memory bounded)
        if self.kw:
            for w in [w for w, st in self.kw.items() if st.head >= len(st.ms)]:
                del self.kw[w]
        if th is not None:
            before = len(self.kw)
            for w in [w for w, st in self.kw.items() if st.S < th]:
                del self.kw[w]
            self.dropped_events += before - len(self.kw)
        if len(self.kw) > self.peak_kw:
            self.peak_kw = len(self.kw)
        top = heapq.nsmallest(cfg.k, self.kw.items(), key=lambda kv: (-kv[1].S, kv[0]))
        top = [(w, st.S) for w, st in top]
        entries = {}
        for w, _ in top:
            if self.snap_i == 0:
                entries[w] = "initial"
            elif w not in self.prev_top:
                entries[w] = "first" if w not in self.seen_top else "re-entry"
        self.prev_top = {w for w, _ in top}
        self.seen_top |= self.prev_top
        burst = self._burst_table(t, [w for w, _ in top])
        snap = {"i": self.snap_i, "t": t, "t_iso": _iso(t), "pos": self.pos,
                "keywords": len(self.kw),
                "top": [{"kw": w, "s": round(s, 4), "e": entries.get(w)} for w, s in top],
                "burst": burst["rows"], "burst_status": burst["status"],
                "burst_reason": burst.get("reason")}
        self.snapshots.append(snap)
        self.snap_i += 1
        self._autotrack(top)
        return snap

    def _burst_table(self, t: int, top_words: list[str]) -> dict:
        cfg = self.cfg
        ok, reason = self._coverage_ok(t)
        den_r = self._interval_count(t - self._b, t)
        den_p = self._interval_count(t - 2 * self._b, t - self._b)
        if not ok:
            return {"rows": [], "status": "deferred", "reason": reason}
        if den_r < cfg.min_articles_per_interval or den_p < cfg.min_articles_per_interval:
            return {"rows": [], "status": "deferred", "reason": "denominator",
                    "den_recent": den_r, "den_prev": den_p}
        cand = []
        for w, st in self.kw.items():
            num_r = len(st.ms) - st.p72
            if num_r >= cfg.min_keyword_articles:
                cand.append((w, num_r, st.p72 - st.p144))
        cand.sort(key=lambda x: (-x[1], x[0]))
        rows = []
        for w, num_r, num_p in cand[:80]:
            arts_r = self._kw_interval_articles(w, t - self._b, t)
            outlets = {self.articles[i].outlet for i in arts_r}
            countries = {self.articles[i].country for i in arts_r}
            if len(outlets) < cfg.min_keyword_outlets:
                continue
            diff = 100.0 * (num_r / den_r - num_p / den_p)
            rows.append({"kw": w, "num_recent": num_r, "den_recent": den_r,
                         "num_prev": num_p, "den_prev": den_p,
                         "diff_pp": round(diff, 2), "outlets": len(outlets),
                         "countries": len(countries)})
        rows.sort(key=lambda r: (-r["diff_pp"], r["kw"]))
        return {"rows": rows[:12], "status": "ok"}

    def _autotrack(self, top) -> None:
        for w, _ in top:
            if w not in self.trackers:
                if len(self.trackers) >= self.cfg.max_trackers:
                    victims = [(tr.last_active, x) for x, tr in self.trackers.items()
                               if not tr.pinned and x not in self.prev_top]
                    if not victims:
                        return
                    del self.trackers[min(victims)[1]]
                self.trackers[w] = Tracker(self.pos, DGIM(self.cfg.dgim_n, self.cfg.dgim_r),
                                           ExactWindowCount(self.cfg.dgim_n), False, self.snap_i)

    # ------------------------------------------------------------------ queries
    def ranking_rows(self, n: int | None = None) -> list[dict]:
        snap = self.snapshots[-1] if self.snapshots else None
        if snap is None:
            return []
        prev = {r["kw"]: i + 1 for i, r in enumerate(self.snapshots[-2]["top"])} \
            if len(self.snapshots) > 1 else {}
        burst_by_kw = {r["kw"]: r for r in snap["burst"]}
        t = snap["t"]
        rows = []
        for rank, r in enumerate(snap["top"][: n or self.cfg.k], 1):
            w = r["kw"]
            arts = self._kw_interval_articles(w, t - self._win, t)
            outlets = {self.articles[i].outlet for i in arts}
            countries = {self.articles[i].country for i in arts if self.articles[i].country != "unknown"}
            b = burst_by_kw.get(w)
            rows.append({
                "rank": rank, "keyword": w, "score": r["s"], "entry": r["e"],
                "prev_rank": prev.get(w),
                "delta": (prev[w] - rank) if w in prev else None,
                "outlets": len(outlets), "countries": len(countries),
                "mentions_7d": len(arts),
                "burst": ({"diff_pp": b["diff_pp"], "num_recent": b["num_recent"],
                           "den_recent": b["den_recent"], "num_prev": b["num_prev"],
                           "den_prev": b["den_prev"], "outlets": b["outlets"]}
                          if b else None),
                "evidence": [self.articles[i].i for i in arts[-3:]][::-1],
            })
        return rows

    def keyword_detail(self, w: str) -> dict:
        w = w.lower()
        t = self.now_t
        st = self.kw.get(w)
        posts = self.postings.get(w, array("i"))
        snap = self.snapshots[-1] if self.snapshots else None
        rank = None
        if snap:
            for i, r in enumerate(snap["top"], 1):
                if r["kw"] == w:
                    rank = i
        arts7 = self._kw_interval_articles(w, t - self._win, t) if t is not None else []
        b = None
        if t is not None:
            ok, reason = self._coverage_ok(t)
            den_r = self._interval_count(t - self._b, t)
            den_p = self._interval_count(t - 2 * self._b, t - self._b)
            num_r = len(st.ms) - st.p72 if st else 0
            num_p = (st.p72 - st.p144) if st else 0
            arts_r = self._kw_interval_articles(w, t - self._b, t)
            outlets_r = {self.articles[i].outlet for i in arts_r}
            status = "ok"
            why = None
            if not ok:
                status, why = "deferred", reason
            elif den_r < self.cfg.min_articles_per_interval or den_p < self.cfg.min_articles_per_interval:
                status, why = "deferred", "denominator"
            elif num_r < self.cfg.min_keyword_articles:
                status, why = "deferred", "keyword_support(recent_articles)"
            elif len(outlets_r) < self.cfg.min_keyword_outlets:
                status, why = "deferred", "keyword_support(outlets)"
            b = {"status": status, "reason": why,
                 "num_recent": num_r, "den_recent": den_r,
                 "num_prev": num_p, "den_prev": den_p,
                 "share_recent_pp": round(100 * num_r / den_r, 3) if den_r else None,
                 "share_prev_pp": round(100 * num_p / den_p, 3) if den_p else None,
                 "diff_pp": round(100 * (num_r / den_r - num_p / den_p), 2)
                 if status == "ok" else None,
                 "outlets_recent": len(outlets_r)}
        return {
            "keyword": w, "as_of": _iso(t) if t is not None else None, "rank": rank,
            "score": st.S if st else 0.0, "in_window": st is not None,
            "mentions_7d": len(arts7),
            "outlets_7d": len({self.articles[i].outlet for i in arts7}),
            "countries_7d": sorted({self.articles[i].country for i in arts7}),
            "first_seen": (_iso(self.obs[posts[0]]) if len(posts) else None),
            "burst": b,
            "dgim": self.dgim_info(w),
            "related": self.related(w, 12),
        }

    def related(self, w: str, limit: int = 10, as_of: int | None = None) -> list[dict]:
        posts = self.postings.get(w.lower())
        if not posts:
            return []
        hi = self.pos - 1 if as_of is None else min(as_of, self.pos - 1)
        out = []
        for i in reversed(posts):
            if i > hi:
                continue
            a = self.articles[i]
            out.append({"id": a.i, "obs": a.obs_iso, "title": a.title, "outlet": a.outlet,
                        "domain": a.domain_std, "country": a.country, "url": a.url,
                        "desc": a.desc[:200]})
            if len(out) >= limit:
                break
        return out

    def dgim_info(self, w: str) -> dict | None:
        tr = self.trackers.get(w.lower())
        if tr is None:
            return None
        t = self.pos - 1
        est = tr.dgim.estimate(t) if t >= tr.start_i else 0.0
        exact = tr.exact.count(t) if t >= tr.start_i else 0
        observed = max(0, min(self.cfg.dgim_n, t - tr.start_i + 1))
        return {"estimate": round(est, 1), "exact": exact,
                "abs_error": round(abs(est - exact), 2),
                "rel_error": round(abs(est - exact) / exact, 4) if exact else 0.0,
                "window": self.cfg.dgim_n, "observed": observed,
                "partial": observed < self.cfg.dgim_n,
                "since_i": tr.start_i,
                "since_obs": _iso(self.obs[tr.start_i]) if tr.start_i < len(self.obs) else None,
                "buckets": tr.dgim.num_buckets,
                "dgim_bits": tr.dgim.memory_bits(), "exact_bits": tr.exact.memory_bits(),
                "bound": tr.dgim.error_bound(), "pinned": tr.pinned}

    def track(self, w: str, pinned: bool = True) -> tuple[bool, str]:
        w = w.lower()
        if w in self.trackers:
            if pinned:
                self.trackers[w].pinned = True
            return True, "이미 추적 중입니다."
        if len(self.trackers) >= self.cfg.max_trackers:
            victims = [(tr.last_active, x) for x, tr in self.trackers.items() if not tr.pinned]
            if not victims:
                return False, "추적 용량이 가득 찼습니다."
            del self.trackers[min(victims)[1]]
        self.trackers[w] = Tracker(self.pos, DGIM(self.cfg.dgim_n, self.cfg.dgim_r),
                                   ExactWindowCount(self.cfg.dgim_n), pinned, self.snap_i)
        return True, "추적을 시작했습니다(이전 기사는 제외, 관측 범위 참고)."

    # ------------------------------------------------------------------ state
    def clock(self) -> dict:
        snap = self.snapshots[-1] if self.snapshots else None
        return {"t": snap["t"] if snap else None,
                "t_iso": snap["t_iso"] if snap else None,
                "pos": self.pos, "total": len(self.articles),
                "snapshot_i": snap["i"] if snap else -1,
                "finished": self.finished,
                "first_obs": _iso(self.obs[0]) if self.obs else None,
                "last_obs": _iso(self.obs[-1]) if self.obs else None}

    def metrics(self) -> dict:
        return {"keywords": len(self.kw), "peak_keywords": self.peak_kw,
                "dropped_events": self.dropped_events,
                "threshold": self.cfg.threshold,
                "trackers": len(self.trackers)}

    def state(self) -> dict:
        snap = self.snapshots[-1] if self.snapshots else None
        return {"clock": self.clock(),
                "config": {k: v for k, v in asdict(self.cfg).items() if k != "gaps"},
                "ranking": self.ranking_rows(),
                "burst": {"rows": snap["burst"] if snap else [],
                          "status": snap["burst_status"] if snap else "idle",
                          "reason": snap.get("burst_reason") if snap else None},
                "metrics": self.metrics()}

    def snapshot_digest(self) -> str:
        """Stable hash over every published snapshot (replay determinism check)."""
        h = hashlib.sha256()
        for s in self.snapshots:
            h.update(json.dumps(s, sort_keys=True).encode("utf-8"))
        return h.hexdigest()

    # ------------------------------------------------------------------ replay all
    def replay_all(self, every: int = 1) -> None:
        """Ingest the whole stream, publishing a snapshot per observation group."""
        n = 0
        while self.pos < len(self.articles):
            self._ingest_group()
            n += 1
            if every > 1 and n % every != 0:
                self.snapshots.pop()      # keep memory bounded for coarse passes
