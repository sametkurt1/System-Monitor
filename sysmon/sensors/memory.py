"""Memory source: ``GlobalMemoryStatusEx`` for physical RAM, ``GetPerformanceInfo``
for commit and cache.

``GlobalMemoryStatusEx`` is the canonical source for total/available physical memory and
is what most system monitors use.  One subtlety: Windows' "Available" already excludes
standby cache, so ``used = total - available`` lines up with Task Manager's *In use*
only loosely.  We therefore also surface the raw ``MemoryLoad`` percentage that Windows
itself reports, and label which one we display.

Commit and cache come from ``GetPerformanceInfo`` rather than the PDH
``\\Memory\\Committed Bytes`` counter: the counter reads back as 0 on several installs,
whereas ``GetPerformanceInfo`` is a documented kernel32 call that always works.  Its
commit fields are in *pages*, so they are scaled by ``PageSize``.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Dict, Optional

from ..models import MemorySnapshot
from .base import Capability, CapabilityState, OK, error

if sys.platform == "win32":
    import ctypes.wintypes as w

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", w.DWORD),
            ("dwMemoryLoad", w.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    class _PERFORMANCE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("cb", w.DWORD),
            ("CommitTotal", ctypes.c_ulonglong),
            ("CommitLimit", ctypes.c_ulonglong),
            ("CommitPeak", ctypes.c_ulonglong),
            ("PhysicalTotal", ctypes.c_ulonglong),
            ("PhysicalAvailable", ctypes.c_ulonglong),
            ("SystemCache", ctypes.c_ulonglong),
            ("KernelTotal", ctypes.c_ulonglong),
            ("KernelPaged", ctypes.c_ulonglong),
            ("KernelNonpaged", ctypes.c_ulonglong),
            ("PageSize", ctypes.c_ulonglong),
            ("HandleCount", w.DWORD),
            ("ProcessCount", w.DWORD),
            ("ThreadCount", w.DWORD),
        ]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MEMORYSTATUSEX)]
    _k32.GlobalMemoryStatusEx.restype = w.BOOL

    # GetPerformanceInfo lives in psapi.dll; some builds also export it from
    # kernel32, so try both rather than assuming.
    _get_performance_info = None
    for _lib in ("kernel32", "psapi"):
        try:
            _dll = ctypes.WinDLL(_lib, use_last_error=True)
            _fn = _dll.GetPerformanceInfo
        except (OSError, AttributeError):
            continue
        _fn.argtypes = [ctypes.POINTER(_PERFORMANCE_INFORMATION), w.DWORD]
        _fn.restype = w.BOOL
        _get_performance_info = _fn
        break


class MemorySource:
    name = "globalmemorystatusex"

    def __init__(self) -> None:
        self._ready = False
        self._states: Dict[Capability, CapabilityState] = {}

    def capabilities(self) -> Dict[Capability, CapabilityState]:
        if self._states:
            return self._states
        if sys.platform != "win32":
            self._states = {Capability.MEMORY: error("GlobalMemoryStatusEx is Windows-only")}
            return self._states
        self._states = {Capability.MEMORY: OK}
        return self._states

    def start(self) -> None:
        # Both APIs need no open handle and no warm-up, so there is nothing to do
        # beyond marking the provider usable.
        if sys.platform == "win32":
            self.capabilities()
            self._ready = True

    def stop(self) -> None:
        self._ready = False

    def sample(self) -> Optional[MemorySnapshot]:
        if sys.platform != "win32":
            return None

        ms = _MEMORYSTATUSEX()
        ms.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not _k32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            return None

        total = int(ms.ullTotalPhys)
        avail = int(ms.ullAvailPhys)
        used = max(0, total - avail)
        # dwMemoryLoad is Windows' own figure; trust it over a hand-rolled ratio when
        # it disagrees materially (it accounts for reserved pages we cannot see).
        load = float(ms.dwMemoryLoad)
        usage = load if 0.0 <= load <= 100.0 else (100.0 * used / total if total else None)

        commit = limit = cache = None
        if _get_performance_info is not None:
            perf = _PERFORMANCE_INFORMATION()
            perf.cb = ctypes.sizeof(_PERFORMANCE_INFORMATION)
            try:
                ok = _get_performance_info(ctypes.byref(perf),
                                           ctypes.sizeof(_PERFORMANCE_INFORMATION))
            except Exception:
                ok = False
            if ok:
                page = int(perf.PageSize) or 4096
                # Commit totals are page counts, not bytes.
                commit = int(perf.CommitTotal) * page
                limit = int(perf.CommitLimit) * page
                cache = int(perf.SystemCache) * page

        from ..cleaner import get_cleanable_cache_bytes
        standby = get_cleanable_cache_bytes()
        if standby is None and cache is not None:
            standby = cache

        return MemorySnapshot(
            total_bytes=total,
            used_bytes=used,
            available_bytes=avail,
            usage=usage,
            committed_bytes=commit,
            commit_limit_bytes=limit,
            cached_bytes=cache,
            standby_bytes=standby,
        )
