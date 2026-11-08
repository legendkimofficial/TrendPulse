"""Memory measurement helpers.

Three levels are reported separately (feedback 2026-11-08, section 3):
  1. logical size  : keyword counts / bit models (algorithm-level, implementation independent)
  2. Python objects: ``deep_sizeof`` of the counting structures only
  3. process       : resident set size of the whole Python process (``process_rss``)
"""
from __future__ import annotations

import ctypes
import sys
from collections import deque
from typing import Any, Iterable

_ATOMIC = (int, float, str, bytes, bool, type(None))


def deep_sizeof(roots: Iterable[Any], seen: set[int] | None = None) -> int:
    """``sys.getsizeof`` applied recursively; every object is counted once.

    Follows dict/list/tuple/set/deque contents and the attributes of objects with
    ``__dict__`` or ``__slots__``. Shared objects (e.g. keyword strings also held
    by the article list) are counted once *within this call*; pass the same
    ``seen`` set to several calls to avoid double counting across them.
    """
    seen = set() if seen is None else seen
    total = 0
    stack = list(roots)
    while stack:
        obj = stack.pop()
        oid = id(obj)
        if oid in seen:
            continue
        seen.add(oid)
        total += sys.getsizeof(obj)
        if isinstance(obj, _ATOMIC):
            continue
        if isinstance(obj, dict):
            stack.extend(obj.keys())
            stack.extend(obj.values())
        elif isinstance(obj, (list, tuple, set, frozenset, deque)):
            stack.extend(obj)
        else:
            d = getattr(obj, "__dict__", None)
            if d is not None:
                stack.append(d)
            for cls in type(obj).__mro__:
                for name in getattr(cls, "__slots__", ()):
                    if hasattr(obj, name):
                        stack.append(getattr(obj, name))
    return total


def process_rss() -> int | None:
    """Resident set size of this process in bytes (Windows and Linux/macOS)."""
    if sys.platform == "win32":
        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
            return int(pmc.WorkingSetSize)
        return None
    try:
        with open("/proc/self/statm") as fh:
            import os
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except OSError:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return ru if sys.platform == "darwin" else ru * 1024
