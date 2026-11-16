"""Time-decaying windows over the GKG key stream (textbook §4.7, decay in stream time).

TimeDecayWindow
---------------
  score_t(key) = sum over mentions i of 2^(-(t - ts_i) / h)        (h = half-life)

  One number per key, no per-article history. A global reference time ``ref`` is kept
  and each key stores ``v = score_t * 2^((t - ref)/h)``, which does not change while time
  passes. Adding a mention at ``ts`` is ``v += 2^((ts - ref)/h)``: O(1), and the weight is
  computed once per batch because a whole 15-minute file shares one ts.
  Before ``2^((ts - ref)/h)`` gets large (``REBASE_HALF_LIVES`` half-lives) every v is
  multiplied by 2^-n for an integer n (exact in binary floating point) and ``ref`` moves
  forward by n half-lives.

  All keys share the same factor, so the order of stored values equals the order of
  current scores. A snapshot therefore ranks only
      (previous Top M)  ∪  (keys updated since the previous snapshot)
  and the Top K (K <= M) of that pool equals the Top K of a full sort: a key outside the
  pool was ranked below M keys last time, its value has not changed, and theirs can only
  have grown (rebasing keeps the order; pruning removes a pooled key only together with
  every key ranked below it).

  Ties (equal stored value) are broken by key, alphabetically ascending.

  Pruning (``threshold`` set): at most once per ``prune_every_hours`` of stream time, at a
  snapshot, one pass deletes keys whose current score < threshold. A deleted key starts
  again from 0, so the pruned score never exceeds the exact score (always an
  under-estimate). ``threshold=None`` is the exact baseline.

  bias_corrected_rate(key, t) = score * ln2/h / (1 - 2^(-elapsed/h))   [mentions per hour]
  where elapsed = t - stream start. For a constant rate r, score -> r*h/ln2 in the steady
  state and r*h/ln2*(1 - 2^(-elapsed/h)) after ``elapsed``; the denominator corrects the
  part that has not built up yet. None while elapsed <= 0.

GkgEngine
---------
  Three windows (half-life 72 h, 168 h, 720 h) times the configured thresholds
  ("variants"). Articles with the same ts form a batch; after a batch is complete, a
  snapshot is taken if ``snapshot_every_minutes`` of stream time have passed since the
  previous one. Run one (window, θ) combination per engine to bound memory in experiments.

Detection (at every snapshot; rates are bias_corrected_rate of ``detect_variant``)
---------
  New issues (72 h):  lift = (rate_72h + α) / (rate_168h + α), α = 1.
    * Candidates: only the 72 h window's Top ``new_candidates`` (2,000). Approximation: a key
      with a high lift but a 72 h score below the 2,000th is never considered.
    * Conditions: >= 20 articles and >= 5 distinct outlets in the last 72 h, counted by
      ``ActivityTracker`` (see there): tracking starts when the key's 72 h score reaches 3,
      so both counts are under-estimated by at most 5; the outlet count is capped at 8.
      The article count uses whole UTC hours (71-72 h of history).
    * Top K by lift (ties by key) = new issues. A key in this Top K that was not in the
      previous judged Top K gives an entry event: "first" or "re-entry".
    * Before 7 days of stream: warming_up (판별 준비 중), no list and no entry events
      (the largest possible lift at day 7 is 1.46; see summary.md "설계 변경").
  Burst / steady (30 days): ratio = (rate_168h + α) / (rate_720h + α), F = 1.5, for the
    168 h Top ``trend_k`` keys: ratio >= 1.5 rising (최근 급증), 1/1.5 <= ratio < 1.5 steady
    (꾸준), ratio < 1/1.5 cooling (식는 중). Before 21 days of stream: warming_up
    (판별 준비 중); the largest possible ratio at day 21 is 1.88 > 1.5.
  Gaps: for each window, the share of missing 15-minute slots in its recent span (72 h,
    168 h, 720 h, clipped at the stream start; a slot counts as present if it had an
    article). Shown always; a judgement is held as insufficient (근거 부족) only when a
    window it uses exceeds 5 %. Warm-up is checked first. A held (warming_up or
    insufficient) new-issue snapshot records no events and does not replace the
    "previous Top K" used for entry events.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from typing import Callable, Iterable

LN2 = math.log(2.0)
REBASE_HALF_LIVES = 60          # 2^60 ≈ 1e18, far below float overflow and precise enough
HALF_LIVES = (("72h", 72.0), ("168h", 168.0), ("720h", 720.0))
FLOAT_BYTES = sys.getsizeof(1.0)


def _rank_key(kv):
    return (-kv[1], kv[0])


class TimeDecayWindow:
    def __init__(self, half_life_hours: float, threshold: float | None = None, *,
                 top_m: int = 2000, prune_every_hours: float = 6.0) -> None:
        if half_life_hours <= 0:
            raise ValueError("half_life_hours must be > 0")
        if threshold is not None and threshold <= 0:
            raise ValueError("threshold must be > 0 or None")
        self.half_life_hours = float(half_life_hours)
        self.h = self.half_life_hours * 3600.0          # seconds
        self.threshold = threshold
        self.top_m = top_m
        self.prune_every = prune_every_hours * 3600.0
        self._v: dict[str, float] = {}
        self._ref: float | None = None
        self.start: float | None = None
        self._w_ts: float | None = None
        self._w = 1.0
        self._dirty: set[str] = set()
        self._ranked: list[tuple[str, float]] = []      # Top M as (key, stored value)
        self.snap_t: float | None = None
        self._last_prune: float | None = None
        self.deleted_total = 0
        self.prunes = 0
        self.rebases = 0
        self.peak_keys = 0

    # ------------------------------------------------------------------ update
    def _weight(self, ts: float) -> float:
        if ts != self._w_ts:
            e = (ts - self._ref) / self.h
            if e > REBASE_HALF_LIVES:
                self._rebase(int(e))
                e = (ts - self._ref) / self.h
            self._w = 2.0 ** e
            self._w_ts = ts
        return self._w

    def _rebase(self, n: int) -> None:
        """Multiply every stored value by 2^-n (exact) and move ref forward n half-lives."""
        d = self._v
        for k in d:
            d[k] = math.ldexp(d[k], -n)
        self._ranked = [(k, math.ldexp(v, -n)) for k, v in self._ranked]
        self._ref += n * self.h
        self._w_ts = None
        self.rebases += 1

    def add(self, keys: Iterable[str], ts: float) -> None:
        if self._ref is None:
            self._ref = self.start = self._last_prune = float(ts)
        w = self._weight(ts)
        d = self._v
        get = d.get
        for k in keys:
            d[k] = get(k, 0.0) + w
        self._dirty.update(keys)

    # ------------------------------------------------------------------ query
    def _factor(self, t: float) -> float:
        """current score = stored value * factor(t)."""
        return 2.0 ** (-(t - self._ref) / self.h)

    def score(self, key: str, t: float) -> float:
        if self._ref is None:
            return 0.0
        return self._v.get(key, 0.0) * self._factor(t)

    def bias_corrected_rate(self, key: str, t: float) -> float | None:
        return self.rate_from_score(self.score(key, t), t)

    def rate_from_score(self, score: float, t: float) -> float | None:
        if self.start is None:
            return None
        elapsed_h = (t - self.start) / 3600.0
        if elapsed_h <= 0:
            return None
        hh = self.half_life_hours
        return score * LN2 / hh / (1.0 - 2.0 ** (-elapsed_h / hh))

    def __len__(self) -> int:
        return len(self._v)

    def __contains__(self, key: str) -> bool:
        return key in self._v

    # ------------------------------------------------------------------ snapshot
    def snapshot(self, t: float) -> None:
        """Prune if due, then rank (previous Top M ∪ updated keys) into the new Top M."""
        self.peak_keys = max(self.peak_keys, len(self._v))
        if (self.threshold is not None and self._last_prune is not None
                and t - self._last_prune >= self.prune_every):
            self.prune(t)
        d = self._v
        pool = {k for k, _ in self._ranked}
        pool.update(self._dirty)
        items = [(k, d[k]) for k in pool if k in d]
        items.sort(key=_rank_key)
        self._ranked = items[:self.top_m]
        self._dirty = set()
        self.snap_t = t

    def prune(self, t: float) -> int:
        """Delete keys whose score at t is below threshold; rebuilds the dict (frees memory)."""
        if self.threshold is None or self._ref is None:
            return 0
        bound = self.threshold / self._factor(t)
        before = len(self._v)
        self._v = {k: v for k, v in self._v.items() if v >= bound}
        dead = before - len(self._v)
        self.deleted_total += dead
        self.prunes += 1
        self._last_prune = t
        return dead

    def top(self, k: int, t: float | None = None) -> list[tuple[str, float]]:
        """Top k from the last snapshot as (key, score at t; default the snapshot time)."""
        if k > self.top_m:
            raise ValueError(f"k={k} exceeds top_m={self.top_m}")
        if self.snap_t is None:
            return []
        f = self._factor(self.snap_t if t is None else t)
        return [(key, v * f) for key, v in self._ranked[:k]]

    def full_top(self, k: int, t: float) -> list[tuple[str, float]]:
        """Reference: full sort of every key (O(n log n); for tests and checks)."""
        f = self._factor(t)
        items = sorted(self._v.items(), key=_rank_key)[:k]
        return [(key, v * f) for key, v in items]

    def memory_bytes(self) -> dict:
        """Estimated bytes of the counting structure (key strings reported separately)."""
        n = len(self._v)
        dict_b = sys.getsizeof(self._v)
        return {"keys": n, "dict_bytes": dict_b, "float_bytes": n * FLOAT_BYTES,
                "estimate_bytes": dict_b + n * FLOAT_BYTES}


# --------------------------------------------------------------------------- detection parts
LABELS_KO = {"rising": "최근 급증", "steady": "꾸준", "cooling": "식는 중",
             "warming_up": "판별 준비 중", "insufficient": "근거 부족"}


def smoothed_ratio(num_rate: float, den_rate: float, alpha: float = 1.0) -> float:
    """(num + α) / (den + α). lift = smoothed_ratio(rate_72h, rate_168h);
    trend ratio = smoothed_ratio(rate_168h, rate_720h)."""
    return (num_rate + alpha) / (den_rate + alpha)


def trend_label(ratio: float, factor: float = 1.5) -> str:
    """ratio >= F -> rising; 1/F <= ratio < F -> steady; ratio < 1/F -> cooling (default F = 1.5)."""
    if ratio >= factor:
        return "rising"
    if ratio >= 1.0 / factor:
        return "steady"
    return "cooling"


class KeyActivity:
    __slots__ = ("hours", "outlets")

    def __init__(self) -> None:
        self.hours: list[list[int]] = []        # [[UTC hour number, articles], ...] ascending
        self.outlets: dict[str, int] = {}       # domain -> last ts (at most max_outlets)


class ActivityTracker:
    """Recent article and outlet counts for keys that are "hot" in the 72 h window.

    A key is tracked from the article at which its 72 h score (after adding that article)
    reaches ``activate_score``. Mentions in the last 72 h each weigh >= 2^-1 in a 72 h
    half-life score, so before activation there were fewer than 2*activate_score of them:
    counts are under-estimated by at most 2*activate_score - 1 (5 for the default 3).

    * articles: hourly buckets (UTC clock hours); ``articles(key, t)`` counts the buckets
      H-71 .. H where H is the hour of t, i.e. between 71 and 72 hours of history.
    * outlets: at most ``max_outlets`` (8) domains with their last-seen ts; when full, the
      domain seen longest ago is evicted. ``outlets(key, t)`` counts domains seen in
      (t - 72 h, t]. Because the most recent domains are kept, the count is exact up to 8
      for the tracked period; it never exceeds 8, which is enough for a ">= 5" test.
    * ``sweep(t)`` drops keys with no article in the window (they re-activate later from 0).
    """

    def __init__(self, window_hours: int = 72, activate_score: float = 3.0, max_outlets: int = 8) -> None:
        self.window_hours = int(window_hours)
        self.activate_score = activate_score
        self.max_outlets = max_outlets
        self.items: dict[str, KeyActivity] = {}
        self.activations = 0
        self.swept = 0

    def observe(self, key: str, ts: int, domain: str, score_now: float) -> None:
        act = self.items.get(key)
        if act is None:
            if score_now < self.activate_score:
                return
            act = self.items[key] = KeyActivity()
            self.activations += 1
        hour = ts // 3600
        hours = act.hours
        if hours and hours[-1][0] == hour:
            hours[-1][1] += 1
        else:
            hours.append([hour, 1])
            lo = hour - self.window_hours + 1
            while hours[0][0] < lo:
                hours.pop(0)
        out = act.outlets
        if domain in out or len(out) < self.max_outlets:
            out[domain] = ts
        else:
            del out[min(out, key=out.__getitem__)]
            out[domain] = ts

    def __contains__(self, key: str) -> bool:
        return key in self.items

    def articles(self, key: str, t: int) -> int:
        act = self.items.get(key)
        if act is None:
            return 0
        lo = t // 3600 - self.window_hours + 1
        return sum(c for h, c in act.hours if h >= lo)

    def outlets(self, key: str, t: int) -> int:
        act = self.items.get(key)
        if act is None:
            return 0
        lo = t - self.window_hours * 3600
        return sum(1 for last in act.outlets.values() if last > lo)

    def sweep(self, t: int) -> int:
        lo = t // 3600 - self.window_hours + 1
        dead = [k for k, act in self.items.items() if not act.hours or act.hours[-1][0] < lo]
        for k in dead:
            del self.items[k]
        self.swept += len(dead)
        return len(dead)

    def memory_bytes(self) -> dict:
        b = sys.getsizeof(self.items)
        buckets = outlets = 0
        for k, act in self.items.items():
            b += sys.getsizeof(act) + sys.getsizeof(act.hours) + sys.getsizeof(act.outlets)
            b += sum(sys.getsizeof(x) + sys.getsizeof(x[0]) for x in act.hours)
            buckets += len(act.hours)
            outlets += len(act.outlets)
        return {"tracked_keys": len(self.items), "hour_buckets": buckets, "outlet_entries": outlets,
                "activations": self.activations, "swept": self.swept, "estimate_bytes": b,
                "note": "key and domain strings are shared with the stream; not counted"}


class SlotCoverage:
    """Which 15-minute slots since the stream start had articles (one byte per slot)."""

    def __init__(self, slot_seconds: int = 900) -> None:
        self.slot = slot_seconds
        self.start: int | None = None
        self.present = bytearray()

    def mark(self, ts: int) -> None:
        if self.start is None:
            self.start = ts - ts % self.slot
        i = (ts - self.start) // self.slot
        if i < 0:
            return
        if i >= len(self.present):
            self.present.extend(b"\0" * (i + 1 - len(self.present)))
        self.present[i] = 1

    def missing_ratio(self, t: int, window_hours: float) -> tuple[float, int]:
        """(share of missing slots, expected slots) in the window ending at the slot of t.

        The window is clipped at the stream start: time before the first article is not
        counted as missing (the bias-corrected rate already accounts for it)."""
        if self.start is None or t < self.start:
            return 0.0, 0
        end = (t - self.start) // self.slot
        lo = max(0, end - int(window_hours * 3600 // self.slot) + 1)
        expected = end - lo + 1
        have = sum(self.present[lo:end + 1])
        return (expected - have) / expected, expected


# --------------------------------------------------------------------------- engine
@dataclass(frozen=True)
class GkgEngineConfig:
    windows: tuple[tuple[str, float], ...] = HALF_LIVES     # (name, half-life hours)
    thresholds: tuple[float | None, ...] = (None,)           # None = exact
    top_m: int = 2000
    snapshot_every_minutes: float = 60.0
    prune_every_hours: float = 6.0
    evidence_path: str | None = None                         # sqlite file; None = no evidence
    evidence_top: int = 200
    evidence_per_day_cap: int | None = 30                    # postings per tracked key per UTC day
    # detection (runs when the 72h/168h and/or 168h/720h windows exist for detect_variant)
    detect: bool = True
    detect_variant: str = "exact"
    alpha: float = 1.0
    new_k: int = 10
    new_warmup_days: float = 7.0                             # new issues held before this
    new_candidates: int = 2000                               # from the 72h Top M (capped at top_m)
    new_min_articles: int = 20
    new_min_outlets: int = 5
    activate_score: float = 3.0
    max_outlets: int = 8
    trend_k: int = 10                                        # labelled keys: 168h Top K
    trend_factor: float = 1.5
    trend_warmup_days: float = 21.0
    gap_limit: float = 0.05


def variant_name(threshold: float | None) -> str:
    return "exact" if threshold is None else f"theta={threshold:g}"


class GkgEngine:
    def __init__(self, config: GkgEngineConfig | None = None,
                 on_snapshot: Callable[["GkgEngine", float], None] | None = None) -> None:
        self.config = cfg = config or GkgEngineConfig()
        if cfg.evidence_top > cfg.top_m:
            raise ValueError("evidence_top must be <= top_m")
        self.windows: dict[tuple[str, str], TimeDecayWindow] = {}
        for name, hl in cfg.windows:
            for th in cfg.thresholds:
                self.windows[(name, variant_name(th))] = TimeDecayWindow(
                    hl, th, top_m=cfg.top_m, prune_every_hours=cfg.prune_every_hours)
        self.evidence = None
        if cfg.evidence_path:
            from trendpulse.gkg_evidence import EvidenceStore
            self.evidence = EvidenceStore(cfg.evidence_path, per_day_cap=cfg.evidence_per_day_cap)
        self.on_snapshot = on_snapshot
        self.articles = 0
        self.snapshots = 0
        self._batch_ts: int | None = None
        self._last_snap: float | None = None
        self.interval = cfg.snapshot_every_minutes * 60.0
        # detection
        v = cfg.detect_variant
        self.half_lives = dict(cfg.windows)
        self._w72 = self.windows.get(("72h", v))
        self._w168 = self.windows.get(("168h", v))
        self._w720 = self.windows.get(("720h", v))
        self.coverage = SlotCoverage()
        self.activity: ActivityTracker | None = None
        if cfg.detect and self._w72 is not None and self._w168 is not None:
            self.activity = ActivityTracker(int(self.half_lives["72h"]), cfg.activate_score, cfg.max_outlets)
        self.detection: dict | None = None         # result of the latest snapshot
        self.new_events: list[dict] = []           # entry events into the new-issue Top K
        self._prev_new: list[str] = []             # new-issue Top K of the last judged snapshot
        self._ever_new: set[str] = set()

    # ------------------------------------------------------------------ stream
    def process(self, a) -> None:
        if self._batch_ts is not None and a.ts != self._batch_ts:
            self._end_batch()
        self._batch_ts = a.ts
        keys, ts = a.keys, a.ts
        for w in self.windows.values():
            w.add(keys, ts)
        self.coverage.mark(ts)
        if self.activity is not None:
            w72 = self._w72
            d, inv = w72._v, 1.0 / w72._w          # score at ts = stored value / weight(ts)
            observe, dom = self.activity.observe, a.domain
            for k in keys:
                v = d.get(k)
                if v is not None:
                    observe(k, ts, dom, v * inv)
        if self.evidence is not None:
            self.evidence.add_article(a)
        self.articles += 1

    def run(self, articles: Iterable) -> "GkgEngine":
        for a in articles:
            self.process(a)
        self.finish()
        return self

    def _end_batch(self) -> None:
        t = self._batch_ts
        if self._last_snap is None or t - self._last_snap >= self.interval:
            self.snapshot(t)

    def finish(self) -> None:
        """Close the last batch and take a final snapshot so queries include every article."""
        if self._batch_ts is not None and self._last_snap != self._batch_ts:
            self.snapshot(self._batch_ts)
        if self.evidence is not None:
            self.evidence.flush()

    def snapshot(self, t: float) -> None:
        for w in self.windows.values():
            w.snapshot(t)
        if self.evidence is not None:
            n = self.config.evidence_top
            for (name, variant), w in self.windows.items():
                self.evidence.track([k for k, _ in w.top(n)], t, f"{name}/{variant}")
            self.evidence.flush()
        if self.config.detect:
            self.detection = self._detect(t)
        self._last_snap = t
        self.snapshots += 1
        if self.on_snapshot is not None:
            self.on_snapshot(self, t)

    # ------------------------------------------------------------------ detection
    def gaps(self, t: float) -> dict:
        out = {}
        for name, hl in self.half_lives.items():
            ratio, expected = self.coverage.missing_ratio(int(t), hl)
            out[name] = {"missing_ratio": ratio, "expected_slots": expected,
                         "insufficient": ratio > self.config.gap_limit}
        return out

    def _detect(self, t: float) -> dict:
        cfg = self.config
        gaps = self.gaps(t)
        res = {"t": t, "gaps": gaps, "new": None, "trend": None}
        if self.activity is not None:
            self.activity.sweep(int(t))
            res["new"] = self._detect_new(t, gaps)
        if self._w168 is not None and self._w720 is not None:
            res["trend"] = self._detect_trend(t, gaps)
        return res

    def _detect_new(self, t: float, gaps: dict) -> dict:
        cfg = self.config
        w72, w168, act = self._w72, self._w168, self.activity
        start = w72.start
        elapsed_days = (t - start) / 86400.0 if start is not None else 0.0
        if elapsed_days < cfg.new_warmup_days:
            return {"status": "warming_up", "label": LABELS_KO["warming_up"], "elapsed_days": elapsed_days,
                    "top": [], "events": []}
        if gaps["72h"]["insufficient"] or gaps["168h"]["insufficient"]:
            return {"status": "insufficient", "label": LABELS_KO["insufficient"], "elapsed_days": elapsed_days,
                    "top": [], "events": []}
        rows = []
        for key, s72 in w72.top(min(cfg.new_candidates, w72.top_m)):
            if key not in act:
                continue
            n = act.articles(key, int(t))
            if n < cfg.new_min_articles:
                continue
            o = act.outlets(key, int(t))
            if o < cfg.new_min_outlets:
                continue
            r72 = w72.rate_from_score(s72, t)
            r168 = w168.bias_corrected_rate(key, t)
            if r72 is None or r168 is None:
                continue
            rows.append({"key": key, "lift": smoothed_ratio(r72, r168, cfg.alpha), "rate_72h": r72,
                         "rate_168h": r168, "articles_72h": n, "outlets_72h": o})
        rows.sort(key=lambda r: (-r["lift"], r["key"]))
        top = rows[:cfg.new_k]
        for i, r in enumerate(top, 1):
            r["rank"] = i
        events = self.record_entries(t, [r["key"] for r in top], {r["key"]: r for r in top})
        return {"status": "ok", "elapsed_days": elapsed_days, "top": top, "events": events,
                "qualified": len(rows)}

    def record_entries(self, t: float, keys: list[str], info: dict | None = None) -> list[dict]:
        """Entry events for keys in this new-issue Top K that were not in the previous judged one."""
        prev = set(self._prev_new)
        events = []
        for rank, k in enumerate(keys, 1):
            if k in prev:
                continue
            ev = {"t": t, "key": k, "kind": "re-entry" if k in self._ever_new else "first", "rank": rank}
            if info and k in info:
                ev["lift"] = info[k]["lift"]
            events.append(ev)
            self._ever_new.add(k)
        self.new_events.extend(events)
        self._prev_new = list(keys)
        return events

    def _detect_trend(self, t: float, gaps: dict) -> dict:
        cfg = self.config
        w168, w720 = self._w168, self._w720
        start = w168.start
        elapsed_days = (t - start) / 86400.0 if start is not None else 0.0
        if elapsed_days < cfg.trend_warmup_days:
            status = "warming_up"
        elif gaps["168h"]["insufficient"] or gaps["720h"]["insufficient"]:
            status = "insufficient"
        else:
            status = "ok"
        rows = []
        for key, s168 in w168.top(cfg.trend_k):
            r168 = w168.rate_from_score(s168, t)
            r720 = w720.bias_corrected_rate(key, t)
            ratio = smoothed_ratio(r168, r720, cfg.alpha) if r168 is not None and r720 is not None else None
            label = trend_label(ratio, cfg.trend_factor) if status == "ok" and ratio is not None else (
                status if status != "ok" else "warming_up")
            rows.append({"key": key, "score_168h": s168, "rate_168h": r168, "rate_720h": r720,
                         "ratio": ratio, "label": label, "label_ko": LABELS_KO[label]})
        return {"status": status, "elapsed_days": elapsed_days, "top": rows}

    # ------------------------------------------------------------------ queries
    def window(self, name: str, variant: str = "exact") -> TimeDecayWindow:
        return self.windows[(name, variant)]

    def top(self, window: str, k: int = 10, variant: str = "exact") -> list[dict]:
        w = self.windows[(window, variant)]
        t = w.snap_t
        return [{"key": key, "score": s, "rate_per_hour": w.rate_from_score(s, t)}
                for key, s in w.top(k)]

    def memory_report(self) -> dict:
        rows = []
        unique: set[str] = set()
        for (name, variant), w in self.windows.items():
            m = w.memory_bytes()
            rows.append({"window": name, "variant": variant, "half_life_hours": w.half_life_hours,
                         "threshold": w.threshold, "peak_keys": max(w.peak_keys, m["keys"]),
                         "deleted_total": w.deleted_total, "prunes": w.prunes, "rebases": w.rebases,
                         **m})
            unique.update(w._v.keys())
        rep = {
            "decay_windows": rows,
            "key_strings": {"unique": len(unique), "bytes": sum(sys.getsizeof(k) for k in unique),
                            "note": "key strings are shared by all windows; counted once"},
        }
        rep["decay_total_bytes"] = sum(r["estimate_bytes"] for r in rows) + rep["key_strings"]["bytes"]
        if self.activity is not None:
            rep["activity_tracker"] = self.activity.memory_bytes()
        rep["slot_coverage_bytes"] = sys.getsizeof(self.coverage.present)
        if self.evidence is not None:
            rep["evidence"] = self.evidence.report()
        return rep

    def close(self) -> None:
        if self.evidence is not None:
            self.evidence.close()
