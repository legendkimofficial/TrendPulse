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

    # ------------------------------------------------------------------ stream
    def process(self, a) -> None:
        if self._batch_ts is not None and a.ts != self._batch_ts:
            self._end_batch()
        self._batch_ts = a.ts
        keys, ts = a.keys, a.ts
        for w in self.windows.values():
            w.add(keys, ts)
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
        self._last_snap = t
        self.snapshots += 1
        if self.on_snapshot is not None:
            self.on_snapshot(self, t)

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
        if self.evidence is not None:
            rep["evidence"] = self.evidence.report()
        return rep

    def close(self) -> None:
        if self.evidence is not None:
            self.evidence.close()
