"""Exponentially decaying window over a stream of keyword sets (MMDS ch.4).

score_t(w) = sum_{i <= t} 1[w in doc_i] * (1 - c) ** (t - i)

Each new document first decays every score by (1 - c) and then adds 1 to every
keyword it contains. With ``threshold`` set, any keyword whose score falls below
it is dropped (the memory-saving variant). With ``threshold=None`` every keyword
ever seen is kept: this is the exact baseline the pruned version is compared with.

Implementation notes
--------------------
* Lazy decay: instead of touching every score per document we store
  ``v(w) = score(w) * g`` where the global factor ``g`` grows by 1/(1-c) per
  document, so decaying *all* scores costs O(1) per document instead of
  O(#keywords). The full per-document cost is still O(#tokens) dict updates,
  plus O(#tokens * log H) heap pushes and amortised pops when pruning (H = heap
  size), plus an occasional O(#keywords) rebase / heap compaction.
  When ``g`` gets huge we rebase all values.
* Pruning per document without a full scan: a min-heap holds (v, w). Since
  "score < threshold" <=> "v < threshold * g", we pop from the heap while its top
  is below that bound. Entries made stale by later updates are skipped.

Error bound of pruning: a keyword is dropped only while its score is < threshold
and it must climb back to >= 1 and decay below threshold again before the next
drop, so the total mass lost is < threshold / (1 - threshold). The pruned score
therefore never exceeds the exact score and is at most that much smaller.
"""
from __future__ import annotations

import heapq
from operator import itemgetter
from typing import Iterable

_REBASE_AT = 1e150
HEAP_COMPACT_FACTOR = 1.2


class DecayingWindow:
    def __init__(self, c: float, threshold: float | None = None):
        if not 0.0 < c < 1.0:
            raise ValueError("c must be in (0, 1)")
        if threshold is not None and not 0.0 < threshold < 1.0:
            # A brand-new keyword enters with score 1; a threshold >= 1 would drop it at once.
            raise ValueError("threshold must be in (0, 1) or None")
        self.c = c
        self.threshold = threshold
        self._growth = 1.0 / (1.0 - c)
        self._g = 1.0
        self._vals: dict[str, float] = {}
        self._heap: list[tuple[float, str]] = []
        self.t = 0                    # documents processed
        self.peak_size = 0
        self.dropped_total = 0

    # ------------------------------------------------------------------ update
    def add(self, keywords: Iterable[str]) -> None:
        """Process one document given its distinct keywords."""
        self.t += 1
        self._g *= self._growth
        g = self._g
        vals = self._vals
        prune = self.threshold is not None
        for w in keywords:
            v = vals.get(w, 0.0) + g
            vals[w] = v
            if prune:
                heapq.heappush(self._heap, (v, w))
        if g > _REBASE_AT:
            self._rebase()
        if prune:
            self._prune()
        if len(vals) > self.peak_size:
            self.peak_size = len(vals)

    def _prune(self) -> None:
        bound = self.threshold * self._g
        heap, vals = self._heap, self._vals
        while heap and heap[0][0] < bound:
            v, w = heapq.heappop(heap)
            if vals.get(w) == v:          # skip entries superseded by a later update
                del vals[w]
                self.dropped_total += 1
        # Compact stale entries. v0.1 used 4x, which let stale entries dominate memory
        # (Python bytes at theta=0.05 exceeded the exact baseline). Measured on the full
        # stream, 1.2x was both smaller and faster (smaller heap -> cheaper push/pop);
        # the rebuild is O(#keywords) and happens only every ~0.2*#keywords pushes.
        if len(heap) > HEAP_COMPACT_FACTOR * len(vals) + 1024:
            self._heap = [(v, w) for w, v in vals.items()]
            heapq.heapify(self._heap)

    def _rebase(self) -> None:
        g = self._g
        self._vals = {w: v / g for w, v in self._vals.items()}
        self._g = 1.0
        if self.threshold is not None:
            self._heap = [(v, w) for w, v in self._vals.items()]
            heapq.heapify(self._heap)

    # ------------------------------------------------------------------ query
    def score(self, w: str) -> float:
        return self._vals.get(w, 0.0) / self._g

    def __contains__(self, w: str) -> bool:
        return w in self._vals

    def __len__(self) -> int:
        return len(self._vals)

    def top_k(self, k: int) -> list[tuple[str, float]]:
        g = self._g
        best = heapq.nlargest(k, self._vals.items(), key=itemgetter(1))
        return [(w, v / g) for w, v in best]

    def items(self):
        g = self._g
        return ((w, v / g) for w, v in self._vals.items())

    def error_bound(self) -> float:
        """Max amount by which a pruned score can under-estimate the exact score."""
        if self.threshold is None:
            return 0.0
        return self.threshold / (1.0 - self.threshold)

    @property
    def heap_size(self) -> int:
        """Entries in the pruning heap, including stale ones (0 for the baseline)."""
        return len(self._heap)
