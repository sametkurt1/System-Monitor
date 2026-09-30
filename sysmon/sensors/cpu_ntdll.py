"""Per-core CPU utilisation from ``NtQuerySystemInformation(SystemProcessorPerformanceInformation)``.

This is the fallback used when the PDH counter stack is unavailable or unhealthy.  It
is a single syscall returning one 48-byte record per logical processor, which makes it
both very cheap and very robust - it keeps working even when the performance-counter
registry is missing entirely, which does happen on stripped or damaged Windows installs.

Two things worth knowing if you copy this elsewhere:

* The information class is **8**.  The widely-copied ``22`` is
  ``SystemPoolTagInformation`` and returns kernel pool tags, not CPU times.
* The record is 48 bytes on x64: five 64-bit tick counters followed by
  ``InterruptCount`` and a spare ``DWORD``.  The 40-byte "Windows 7" layout that
  circulates online is wrong, and the buffer must be passed at exactly the size the
  sizing call reports - an oversized buffer is rejected.
"""

from __future__ import annotations

import ctypes
import sys
from typing import List, Optional, Sequence, Tuple

if sys.platform == "win32":
    import ctypes.wintypes as w

    _ntdll = ctypes.WinDLL("ntdll")

    class _PROCESSOR_PERFORMANCE(ctypes.Structure):
        _fields_ = [
            ("IdleTime", ctypes.c_longlong),
            ("KernelTime", ctypes.c_longlong),
            ("UserTime", ctypes.c_longlong),
            ("DpcTime", ctypes.c_longlong),
            ("InterruptTime", ctypes.c_longlong),
            ("InterruptCount", ctypes.c_long),
            ("Reserved", ctypes.c_uint),
        ]

    _REC = ctypes.sizeof(_PROCESSOR_PERFORMANCE)
    _CLASS = 8  # SystemProcessorPerformanceInformation

    _ntdll.NtQuerySystemInformation.argtypes = [
        w.DWORD, ctypes.c_void_p, w.ULONG, ctypes.POINTER(w.ULONG)]
    _ntdll.NtQuerySystemInformation.restype = ctypes.c_long

STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
STATUS_SUCCESS = 0


class NtdllCpuSource:
    """Cheap per-core utilisation with no dependency on the PDH counter stack."""

    name = "ntquerysysteminformation"

    def __init__(self) -> None:
        self._buf = None
        self._count = 0
        self._prev: List[Tuple[int, int, int]] = []  # (idle, kernel, user)
        self._ready = False
        self.reason = ""

    def available(self) -> bool:
        if sys.platform != "win32":
            self.reason = "ntdll is Windows-only"
            return False
        needed = w.ULONG(0)
        # The sizing call is *expected* to fail with STATUS_INFO_LENGTH_MISMATCH; it
        # reports the required size.  Compare the masked status, not the signed value.
        st = _ntdll.NtQuerySystemInformation(_CLASS, None, 0, ctypes.byref(needed)) & 0xFFFFFFFF
        if st not in (STATUS_SUCCESS, STATUS_INFO_LENGTH_MISMATCH) or needed.value == 0:
            self.reason = f"NtQuerySystemInformation(8) sizing failed: 0x{st:08x}"
            return False
        # The buffer must be passed at exactly the reported size; larger is rejected.
        buf = ctypes.create_string_buffer(needed.value)
        st = _ntdll.NtQuerySystemInformation(_CLASS, buf, needed.value,
                                            ctypes.byref(needed)) & 0xFFFFFFFF
        if st != STATUS_SUCCESS:
            self.reason = f"NtQuerySystemInformation(8) failed: 0x{st:08x}"
            return False
        count = needed.value // _REC
        if count == 0:
            self.reason = "NtQuerySystemInformation(8) returned no records"
            return False
        self._buf = buf
        self._count = count
        self._prev = []
        self._ready = True
        self.reason = ""
        return True

    def stop(self) -> None:
        self._buf = None
        self._count = 0
        self._prev = []
        self._ready = False

    def sample(self) -> Optional[Sequence[Optional[float]]]:
        """Return one percentage per logical processor, or ``None`` on the first call."""
        if not self._ready or self._buf is None:
            return None
        need = w.ULONG(self._count * _REC)
        st = _ntdll.NtQuerySystemInformation(_CLASS, self._buf, self._count * _REC,
                                            ctypes.byref(need)) & 0xFFFFFFFF
        if st != STATUS_SUCCESS:
            return None
        count = min(self._count, need.value // _REC)
        if count <= 0:
            return None

        recs = ctypes.cast(self._buf, ctypes.POINTER(_PROCESSOR_PERFORMANCE * count)).contents
        now = [(recs[i].IdleTime, recs[i].KernelTime, recs[i].UserTime) for i in range(count)]

        out: List[Optional[float]] = []
        if len(self._prev) == count:
            for i in range(count):
                p_idle, p_kern, p_user = self._prev[i]
                idle, kern, user = now[i]
                d_idle = idle - p_idle
                # KernelTime is inclusive of idle time on Windows.
                d_kernel = kern - p_kern
                d_user = user - p_user
                d_busy = (d_kernel - d_idle) + d_user
                d_total = d_kernel + d_user
                out.append(100.0 * d_busy / d_total if d_total > 0 else 0.0)
        else:
            out = [None] * count
        self._prev = now
        return out

    @staticmethod
    def average(values: Sequence[Optional[float]]) -> Optional[float]:
        seen = [v for v in values if v is not None]
        if not seen:
            return None
        return sum(seen) / len(seen)
