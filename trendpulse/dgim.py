"""DGIM: approximate count of 1s among the last N bits of a stream (MMDS 4.6).

Bit ``t`` of a keyword's stream is 1 when document ``t`` contains the keyword.
Only the 1s need to be fed in (``add(t)``); 0s are implicit in the clock, so
updating costs O(log N) per *occurrence*, not per document.

Buckets: (timestamp of the bucket's most recent 1, size = power of two). At most
``r`` buckets of each size are kept; when an (r+1)-th appears, the two oldest of
that size merge. The classic setting is r = 2.

Estimate: sum of all bucket sizes, minus a correction for the oldest bucket,
which is only partly inside the window.
  * "textbook": subtract half its size (MMDS).
  * "midpoint" (default): its most recent 1 is known to be inside the window,
    so the true count lies in [sum - size + 1, sum]; report the midpoint.
Both keep the relative error below 1 / (2 (r - 1)).
"""
from __future__ import annotations

import math


class DGIM:
    __slots__ = ("N", "r", "estimator", "_buckets", "last_t")

    def __init__(self, N: int, r: int = 2, estimator: str = "midpoint"):
        if N < 1:
            raise ValueError("N must be >= 1")
        if r < 2:
            raise ValueError("r must be >= 2")
        if estimator not in ("midpoint", "textbook"):
            raise ValueError("estimator must be 'midpoint' or 'textbook'")
        self.N = N
        self.r = r
        self.estimator = estimator
        # Newest bucket first. Each entry: [timestamp, size].
        self._buckets: list[list[int]] = []
        self.last_t = -1

    def expire(self, t: int) -> None:
        """Drop buckets that fell out of the window ending at ``t``."""
        self._expire(t)

    def _expire(self, t: int) -> None:
        b = self._buckets
        while b and b[-1][0] <= t - self.N:
            b.pop()

    def add(self, t: int) -> None:
        """Record a 1 at time ``t`` (non-decreasing)."""
        if t < self.last_t:
            raise ValueError("timestamps must be non-decreasing")
        if t == self.last_t:
            return            # at most one 1 per position (document frequency)
        self.last_t = t
        self._expire(t)
        b = self._buckets
        b.insert(0, [t, 1])
        # Cascade merges: buckets of equal size are contiguous, sizes grow with age.
        i = 0
        size = 1
        while True:
            j = i
            while j < len(b) and b[j][1] == size:
                j += 1
            count = j - i
            if count <= self.r:
                break
            # merge the two oldest buckets of this size: positions j-2 and j-1
            newer = b[j - 2]
            newer[1] = size * 2           # keeps the newer timestamp
            del b[j - 1]
            i = j - 2                     # the merged bucket starts the next size group
            size *= 2

    def estimate(self, t: int) -> float:
        """Approximate number of 1s in positions (t - N, t]."""
        self._expire(t)
        b = self._buckets
        if not b:
            return 0.0
        total = sum(s for _, s in b)
        oldest = b[-1][1]
        if self.estimator == "textbook":
            return total - oldest / 2.0
        return total - (oldest - 1) / 2.0

    @property
    def num_buckets(self) -> int:
        return len(self._buckets)

    def memory_bits(self) -> int:
        """*Logical* storage model (compact encoding assumed): each bucket stores a
        timestamp mod N and log2(size). The Python objects used here are larger;
        experiments report both (see experiments E3 / E0)."""
        ts_bits = max(1, math.ceil(math.log2(self.N)))
        size_bits = max(1, math.ceil(math.log2(math.log2(self.N) + 1)))
        return len(self._buckets) * (ts_bits + size_bits)

    def error_bound(self) -> float:
        return 1.0 / (2.0 * (self.r - 1))
