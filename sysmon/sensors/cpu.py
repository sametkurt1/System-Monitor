"""CPU source: identity from the registry, utilisation and frequency from PDH.

Windows exposes no supported API for CPU package temperature or power draw, so those
two come from the optional LibreHardwareMonitor provider (see
:mod:`sysmon.sensors.thermal`) and are reported as ``None`` when it is unavailable.

PDH notes that matter (see RESEARCH.md):
  * ``PDH_HANDLE`` is ``ULONG_PTR``; declaring it 32-bit breaks every call.
  * ``PdhAddEnglishCounterW`` is the only locale-independent way to name counters.
  * The ``Processor Information`` object uses ``<group>,<index>`` instance names.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Dict, List, Optional, Tuple

from ..models import CpuCore, CpuSnapshot
from .base import Capability, CapabilityState, OK, error, unavailable
from .cpu_ntdll import NtdllCpuSource

if sys.platform == "win32":
    import ctypes.wintypes as w

    _pdh = ctypes.WinDLL("pdh.dll")
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _PDH_HANDLE = ctypes.c_size_t  # ULONG_PTR

    class _PDH_FMT_COUNTERVALUE(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [
                ("longValue", w.LONG),
                ("doubleValue", ctypes.c_double),
                ("largeValue", ctypes.c_longlong),
                ("AnsiStringValue", ctypes.c_char_p),
                ("WideStringValue", ctypes.c_wchar_p),
            ]

        _fields_ = [("CStatus", w.DWORD), ("value", _U)]

    _pdh.PdhOpenQueryW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p,
                                   ctypes.POINTER(_PDH_HANDLE)]
    _pdh.PdhOpenQueryW.restype = ctypes.c_ulong
    _pdh.PdhAddEnglishCounterW.argtypes = [_PDH_HANDLE, ctypes.c_wchar_p, ctypes.c_void_p,
                                           ctypes.POINTER(_PDH_HANDLE)]
    _pdh.PdhAddEnglishCounterW.restype = ctypes.c_ulong
    _pdh.PdhCollectQueryData.argtypes = [_PDH_HANDLE]
    _pdh.PdhCollectQueryData.restype = ctypes.c_ulong
    _pdh.PdhGetFormattedCounterValue.argtypes = [_PDH_HANDLE, w.DWORD, ctypes.c_void_p,
                                                 ctypes.POINTER(_PDH_FMT_COUNTERVALUE)]
    _pdh.PdhGetFormattedCounterValue.restype = ctypes.c_ulong
    _pdh.PdhCloseQuery.argtypes = [_PDH_HANDLE]
    _pdh.PdhCloseQuery.restype = ctypes.c_ulong

    _PDH_FMT_DOUBLE = 0x200
    _PDH_FMT_LARGE = 0x400
    _ALL_PROCESSOR_GROUPS = 0xFFFF

    _k32.GetActiveProcessorCount.argtypes = [w.WORD]
    _k32.GetActiveProcessorCount.restype = w.DWORD
    _k32.GetActiveProcessorGroupCount.argtypes = []
    _k32.GetActiveProcessorGroupCount.restype = w.WORD


def _logical_processor_slots() -> List[Tuple[int, int]]:
    """Return ``(group, index)`` for every logical processor.

    Uses the documented ``GetActiveProcessorCount``/``GetActiveProcessorGroupCount``
    pair, which is correct across processor groups (>64 logical CPUs).
    """
    if sys.platform != "win32":
        import os
        return [(0, i) for i in range(os.cpu_count() or 1)]
    try:
        ngroups = max(1, int(_k32.GetActiveProcessorGroupCount()))
        slots: List[Tuple[int, int]] = []
        for g in range(ngroups):
            cnt = int(_k32.GetActiveProcessorCount(g))
            slots.extend((g, i) for i in range(cnt))
        return slots
    except Exception:
        import os
        return [(0, i) for i in range(os.cpu_count() or 1)]


def _read_cpu_identity() -> Tuple[str, Optional[str], Optional[float], Optional[float], Optional[int], Optional[int]]:
    """Pull CPU name, vendor, nominal clock, max boost clock and core counts."""
    name = "Unknown CPU"
    vendor: Optional[str] = None
    base_mhz: Optional[float] = None
    try:
        import winreg
        key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            try:
                name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
            except OSError:
                pass
            try:
                vendor = str(winreg.QueryValueEx(key, "VendorIdentifier")[0]).strip()
            except OSError:
                pass
            try:
                base_mhz = float(winreg.QueryValueEx(key, "~MHz")[0])
            except (OSError, TypeError, ValueError):
                pass
    except Exception:
        pass

    from .cpu_specs import lookup_cpu_specs
    base_mhz, boost_mhz = lookup_cpu_specs(name, base_mhz)

    physical: Optional[int] = None
    logical: Optional[int] = len(_logical_processor_slots()) or None

    # Physical core count: ask WMI once. It is a static property, so this is cheap
    # and only runs during start().
    try:
        if sys.platform == "win32":
            import win32com.client  # optional; only used if pywin32 happens to exist
            wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\CIMV2")
            for p in wmi.ExecQuery("SELECT NumberOfCores FROM Win32_Processor"):
                physical = int(p.NumberOfCores)
                break
    except Exception:
        physical = None

    return name, vendor, base_mhz, boost_mhz, physical, logical


class CpuSource:
    """PDH-backed CPU metrics."""

    name = "pdh"

    def __init__(self) -> None:
        self._query = None
        self._total_util = None
        self._per_core: List[Tuple[int, Tuple[int, int], object, object, object]] = []
        self._slots: List[Tuple[int, int]] = []
        self._ready = False
        self._warmup = 0
        self._identity = ("Unknown CPU", None, None, None, None, None)
        self._states: Dict[Capability, CapabilityState] = {}
        # Fallback for machines whose performance-counter stack is missing or damaged.
        self._fallback = NtdllCpuSource()
        self._using_fallback = False
        self._fallback_reason = ""

    # ----------------------------------------------------------- capabilities

    def capabilities(self) -> Dict[Capability, CapabilityState]:
        if self._states:
            return self._states
        if sys.platform != "win32":
            self._states = {
                Capability.CPU_NAME: error("PDH is Windows-only"),
                Capability.CPU_USAGE: error("PDH is Windows-only"),
            }
            return self._states
        try:
            self._identity = _read_cpu_identity()
        except Exception as exc:  # pragma: no cover
            self._states = {Capability.CPU_NAME: error(str(exc))}
            return self._states
        self._states = {
            Capability.CPU_NAME: OK,
            Capability.CPU_USAGE: OK,
            Capability.CPU_PER_CORE: OK,
            Capability.CPU_FREQUENCY: unavailable(
                "Windows reports only the nominal clock for this CPU; no supported "
                "API exposes the live frequency"),
        }
        return self._states

    # --------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._ready or sys.platform != "win32":
            return
        self.capabilities()
        self._slots = _logical_processor_slots()
        self._query = ctypes.c_size_t()
        st = _pdh.PdhOpenQueryW(None, None, ctypes.byref(self._query))
        if st != 0:
            self._query = None
            self._activate_fallback("PdhOpenQuery failed (0x%08x)" % (st & 0xFFFFFFFF))
            return

        total = ctypes.c_size_t()
        if _pdh.PdhAddEnglishCounterW(
            self._query, r"\Processor Information(_Total)\% Processor Utility",
            None, ctypes.byref(total)
        ) == 0:
            self._total_util = total

        for g, i in self._slots:
            util = ctypes.c_size_t()
            freq = ctypes.c_size_t()
            pct_max = ctypes.c_size_t()
            ok_util = _pdh.PdhAddEnglishCounterW(
                self._query,
                rf"\Processor Information({g},{i})\% Processor Utility",
                None, ctypes.byref(util)) == 0
            ok_freq = _pdh.PdhAddEnglishCounterW(
                self._query,
                rf"\Processor Information({g},{i})\Processor Frequency",
                None, ctypes.byref(freq)) == 0
            ok_pct = _pdh.PdhAddEnglishCounterW(
                self._query,
                rf"\Processor Information({g},{i})\% of Maximum Frequency",
                None, ctypes.byref(pct_max)) == 0
            self._per_core.append(((g, i),
                                   util if ok_util else None,
                                   freq if ok_freq else None,
                                   pct_max if ok_pct else None))

        # If the counter set is missing entirely, fall back to ntdll.
        if self._total_util is None and not any(c[1] is not None for c in self._per_core):
            self._activate_fallback("the 'Processor Information' counter set is unavailable")
            return

        self._ready = True
        self._warmup = 0

    def _activate_fallback(self, why: str) -> None:
        """Switch to NtQuerySystemInformation(8) for per-core utilisation."""
        self._using_fallback = True
        if self._fallback.available():
            self._ready = True
            self._warmup = 0
            self._states[Capability.CPU_USAGE] = OK
            self._states[Capability.CPU_PER_CORE] = OK
            self._fallback_reason = f"using NtQuerySystemInformation(8): {why}"
        else:
            self._ready = False
            self._fallback_reason = f"unavailable ({why}); {self._fallback.reason}"
            self._states[Capability.CPU_USAGE] = unavailable(self._fallback_reason)
            self._states[Capability.CPU_PER_CORE] = unavailable(self._fallback_reason)

    def stop(self) -> None:
        if self._query is not None:
            try:
                _pdh.PdhCloseQuery(self._query)
            except Exception:
                pass
        self._query = None
        try:
            self._fallback.stop()
        except Exception:
            pass
        self._ready = False

    # ----------------------------------------------------------------- sample

    def _read(self, handle, fmt: int = _PDH_FMT_DOUBLE if sys.platform == "win32" else 0) -> Optional[float]:
        """Read a counter, honouring the union member implied by ``fmt``."""
        if handle is None:
            return None
        value = _PDH_FMT_COUNTERVALUE()
        st = _pdh.PdhGetFormattedCounterValue(handle, fmt, None, ctypes.byref(value))
        if st != 0:
            return None
        if fmt & 0x400:          # PDH_FMT_LARGE
            return float(value.value.largeValue)
        return float(value.value.doubleValue)

    def sample(self) -> Optional[CpuSnapshot]:
        if not self._ready:
            return None
        if self._using_fallback:
            return self._sample_fallback()
        if self._query is None:
            return None
        if _pdh.PdhCollectQueryData(self._query) != 0:
            return None

        # Rate counters need two samples before the first reading is meaningful.
        self._warmup += 1
        if self._warmup < 2:
            return None

        total = self._read(self._total_util)

        cores: List[CpuCore] = []
        for idx, ((g, i), h_util, h_freq, _pct) in enumerate(self._per_core):
            usage = self._read(h_util)
            freq = self._read(h_freq)
            cores.append(CpuCore(index=idx, usage=usage, frequency_mhz=freq))

        name, vendor, base, boost, physical, logical = self._identity
        return CpuSnapshot(
            name=name,
            vendor=vendor,
            usage=total,
            cores_physical=physical,
            cores_logical=logical,
            frequency_mhz=None,
            frequency_max_mhz=boost,
            frequency_is_nominal=False,
            base_clock_mhz=base,
            per_core=tuple(cores),
        )

    def _sample_fallback(self) -> Optional[CpuSnapshot]:
        """Per-core utilisation from ntdll; semantics are % Processor *Time*
        (not turbo-normalised like % Processor Utility), so figures differ
        slightly from Task Manager when the CPU is boosting."""
        values = self._fallback.sample()
        if values is None:
            return None
        avg = NtdllCpuSource.average(values)
        if avg is None:
            return None
        name, vendor, base, boost, physical, logical = self._identity
        cores = tuple(CpuCore(index=i, usage=v) for i, v in enumerate(values))
        return CpuSnapshot(
            name=name,
            vendor=vendor,
            usage=avg,
            cores_physical=physical,
            cores_logical=logical or len(values),
            frequency_max_mhz=boost,
            base_clock_mhz=base,
            per_core=cores,
        )

    def status_line(self) -> str:
        if self._using_fallback:
            return getattr(self, "_fallback_reason", "") or "ntdll fallback"
        return "PDH (turbo-normalised, Task Manager parity)"
