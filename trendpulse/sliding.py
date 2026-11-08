"""Exact count of 1s in the last N positions, implemented efficiently.

Instead of an N-bit buffer per keyword we keep the positions of the 1s inside
the window (a deque). Cost is O(1) amortised per occurrence; memory is
count * log2(N) bits, which beats an N-bit bitmap for rare keywords. This is the
fair "exact" opponent of DGIM.
"""
from __future__ import annotations

import math
from collections import deque


class ExactWindowCount:
    __slots__ = ("N", "_pos")

    def __init__(self, N: int):
        self.N = N
        self._pos: deque[int] = deque()

    def add(self, t: int) -> None:
        if not self._pos or self._pos[-1] != t:
            self._pos.append(t)
        self._expire(t)

    def expire(self, t: int) -> None:
        self._expire(t)

    def _expire(self, t: int) -> None:
        p = self._pos
        lo = t - self.N
        while p and p[0] <= lo:
            p.popleft()

    def count(self, t: int) -> int:
        self._expire(t)
        return len(self._pos)

    def memory_bits(self) -> int:
        """*Logical* storage model: the cheaper of a positions list (count * log2 N
        bits) or an N-bit bitmap. The implementation always uses a deque; it does
        not actually switch to a bitmap. Python object sizes are reported separately."""
        ts_bits = max(1, math.ceil(math.log2(self.N)))
        return min(len(self._pos) * ts_bits, self.N)
