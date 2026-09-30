"""NVIDIA GPU source: NVML via ctypes, with ``nvidia-smi`` as a fallback.

NVML (``nvml.dll``, shipped in ``System32`` by the NVIDIA driver) needs no third-party
binding and no elevation.  Every function is bound defensively: a driver that does not
implement an entry point yields ``None`` for that field instead of an exception, so the
UI shows ``N/A`` rather than crashing.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Dict, List, Optional

from ..models import GpuSnapshot
from .base import Capability, CapabilityState, OK, error, unavailable

NVML_SUCCESS = 0

# nvmlDeviceGetMemoryInfo's struct gained fields across NVML generations; allocate
# generously so a newer driver writing more fields never overflows our buffer.
_MEM_FIELDS = 4

if sys.platform == "win32":
    class _Utilization(ctypes.Structure):
        _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint),
                    ("encoder", ctypes.c_uint), ("decoder", ctypes.c_uint)]

    class _Memory(ctypes.Structure):
        _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                    ("used", ctypes.c_ulonglong), ("reserved", ctypes.c_ulonglong)]

    class _PciInfo(ctypes.Structure):
        _fields_ = [
            ("busIdLegacy", ctypes.c_int),
            ("domain", ctypes.c_uint),
            ("bus", ctypes.c_uint),
            ("device", ctypes.c_uint),
            ("pciDeviceId", ctypes.c_uint),
            ("pciSubSystemId", ctypes.c_uint),
            ("pciBusId", ctypes.c_int),
        ]


class NvidiaSource:
    name = "nvml"

    CLOCK_GRAPHICS = 0
    CLOCK_SM = 1
    CLOCK_MEMORY = 2
    CLOCK_VIDEO = 3

    def __init__(self) -> None:
        self._nvml = None
        self._devices: List[ctypes.c_void_p] = []
        self._ready = False
        self._driver: Optional[str] = None
        self._reason = ""
        self._states: Dict[Capability, CapabilityState] = {}

    # --------------------------------------------------------------- binding

    def _bind(self) -> None:
        nv = ctypes.WinDLL("nvml.dll")
        self._fn: Dict[str, object] = {}

        def bind(name, argtypes, restype=ctypes.c_int):
            try:
                fn = getattr(nv, name)
            except AttributeError:
                return None
            fn.argtypes = argtypes
            fn.restype = restype
            self._fn[name] = fn
            return fn

        c_void = ctypes.c_void_p
        c_uint = ctypes.c_uint
        bind("nvmlInit_v2", [])
        bind("nvmlShutdown", [])
        bind("nvmlSystemGetDriverVersion", [ctypes.c_char_p, ctypes.c_uint], ctypes.c_int)
        bind("nvmlDeviceGetCount_v2", [ctypes.POINTER(c_uint)])
        bind("nvmlDeviceGetHandleByIndex_v2", [ctypes.c_uint, ctypes.POINTER(c_void)])
        bind("nvmlDeviceGetName", [c_void, ctypes.c_char_p, ctypes.c_uint])
        bind("nvmlDeviceGetUUID", [c_void, ctypes.c_char_p, ctypes.c_uint])
        bind("nvmlDeviceGetUtilizationRates", [c_void, ctypes.POINTER(_Utilization)])
        bind("nvmlDeviceGetTemperature", [c_void, ctypes.c_uint, ctypes.POINTER(c_uint)])
        bind("nvmlDeviceGetPowerUsage", [c_void, ctypes.POINTER(c_uint)])
        bind("nvmlDeviceGetPowerManagementLimit", [c_void, ctypes.POINTER(c_uint)])
        bind("nvmlDeviceGetMemoryInfo", [c_void, ctypes.POINTER(_Memory)])
        bind("nvmlDeviceGetClockInfo", [c_void, ctypes.c_uint, ctypes.POINTER(c_uint)])
        bind("nvmlDeviceGetMaxClockInfo", [c_void, ctypes.c_uint, ctypes.POINTER(c_uint)])
        bind("nvmlDeviceGetFanSpeed", [c_void, ctypes.POINTER(c_uint)])
        bind("nvmlDeviceGetPerformanceState", [c_void, ctypes.POINTER(c_uint)])
        bind("nvmlDeviceGetPciInfo", [c_void, ctypes.POINTER(_PciInfo)])
        self._nvml = nv

    def _call(self, name: str, *args):
        fn = self._fn.get(name)
        if fn is None:
            return None
        try:
            return fn(*args)
        except Exception:
            return None

    def _val(self, name: str, *args) -> Optional[int]:
        out = ctypes.c_uint()
        rc = self._call(name, *args, ctypes.byref(out))
        if rc != NVML_SUCCESS:
            return None
        return int(out.value)

    # ----------------------------------------------------------- capabilities

    def capabilities(self) -> Dict[Capability, CapabilityState]:
        if self._states:
            return self._states
        if sys.platform != "win32":
            self._states = {Capability.GPU: error("NVML is Windows-only")}
            return self._states
        self._states = {Capability.GPU: OK if self._reason == "" else unavailable(self._reason)}
        return self._states

    # --------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._ready or sys.platform != "win32":
            return
        try:
            self._bind()
        except OSError as exc:
            self._reason = f"nvml.dll not found ({exc})"
            self._states = {Capability.GPU: unavailable(self._reason)}
            return

        init = self._fn.get("nvmlInit_v2") or self._fn.get("nvmlInit")
        if init is None:
            self._reason = "NVML entry point missing"
            self._states = {Capability.GPU: unavailable(self._reason)}
            return
        try:
            rc = init()
        except Exception as exc:
            self._reason = f"NVML init failed: {exc}"
            self._states = {Capability.GPU: unavailable(self._reason)}
            return
        if rc != NVML_SUCCESS:
            self._reason = f"NVML init returned {rc} (is the NVIDIA driver installed?)"
            self._states = {Capability.GPU: unavailable(self._reason)}
            return

        buf = ctypes.create_string_buffer(96)
        if self._call("nvmlSystemGetDriverVersion", buf, 96) == NVML_SUCCESS:
            self._driver = buf.value.decode("ascii", "replace")

        count = ctypes.c_uint()
        if self._call("nvmlDeviceGetCount_v2", ctypes.byref(count)) != NVML_SUCCESS:
            count = ctypes.c_uint()
            if self._call("nvmlDeviceGetCount", ctypes.byref(count)) != NVML_SUCCESS:
                self._reason = "NVML device enumeration failed"
                self._states = {Capability.GPU: unavailable(self._reason)}
                return

        for i in range(max(1, int(count.value))):
            dev = ctypes.c_void_p()
            if self._call("nvmlDeviceGetHandleByIndex_v2", i, ctypes.byref(dev)) == NVML_SUCCESS:
                self._devices.append(dev)
            else:
                alt = ctypes.c_void_p()
                if self._call("nvmlDeviceGetHandleByIndex", i, ctypes.byref(alt)) == NVML_SUCCESS:
                    self._devices.append(alt)

        if not self._devices:
            self._reason = "no NVIDIA devices reported by NVML"
            self._states = {Capability.GPU: unavailable(self._reason)}
            return

        self._ready = True
        self._states = {Capability.GPU: OK}

    def stop(self) -> None:
        if self._ready and self._nvml is not None:
            try:
                self._fn.get("nvmlShutdown", lambda: None)()
            except Exception:
                pass
        self._devices = []
        self._ready = False

    # ----------------------------------------------------------------- sample

    def sample(self) -> List[GpuSnapshot]:
        if not self._ready:
            return []
        out: List[GpuSnapshot] = []
        for i, dev in enumerate(self._devices):
            out.append(self._read_device(i, dev))
        return out

    def _read_device(self, index: int, dev) -> GpuSnapshot:
        def s(name: str, size: int = 96) -> Optional[str]:
            b = ctypes.create_string_buffer(size)
            if self._call(name, dev, b, size) == NVML_SUCCESS:
                return b.value.decode("utf-8", "replace")
            return None

        name = s("nvmlDeviceGetName") or "NVIDIA GPU"

        util = _Utilization()
        use_util = False
        if self._call("nvmlDeviceGetUtilizationRates", dev, ctypes.byref(util)) == NVML_SUCCESS:
            use_util = True

        temp = self._val("nvmlDeviceGetTemperature", dev, 0)  # NVML_TEMPERATURE_GPU
        power_mw = self._val("nvmlDeviceGetPowerUsage", dev)
        limit_mw = self._val("nvmlDeviceGetPowerManagementLimit", dev)

        mem = _Memory()
        have_mem = self._call("nvmlDeviceGetMemoryInfo", dev, ctypes.byref(mem)) == NVML_SUCCESS

        clock = self._val("nvmlDeviceGetClockInfo", dev, self.CLOCK_GRAPHICS)
        if clock is None:
            clock = self._val("nvmlDeviceGetClockInfo", dev, self.CLOCK_SM)
        mem_clock = self._val("nvmlDeviceGetClockInfo", dev, self.CLOCK_MEMORY)
        max_clock = self._val("nvmlDeviceGetMaxClockInfo", dev, self.CLOCK_GRAPHICS)
        mem_max_clock = self._val("nvmlDeviceGetMaxClockInfo", dev, self.CLOCK_MEMORY)
        fan = self._val("nvmlDeviceGetFanSpeed", dev)

        link = None
        pci = _PciInfo()
        if self._call("nvmlDeviceGetPciInfo", dev, ctypes.byref(pci)) == NVML_SUCCESS:
            # Modern drivers store the bus id as a 4-character ASCII string in
            # busIdLegacy ("0001"), older ones pack it as (bus << 8) | device.
            raw = int(pci.busIdLegacy) & 0xFFFFFFFF
            text = bytes((raw >> (8 * i)) & 0xFF for i in range(4))
            text = text.split(b"\x00")[0].decode("ascii", "ignore").strip()
            if text.isdigit():
                link = f"PCI {int(text)}"
            else:
                bus = (raw >> 8) & 0xFF
                if bus:
                    link = f"PCI {bus}"

        return GpuSnapshot(
            index=index,
            name=name,
            usage=float(util.gpu) if use_util else None,
            usage_memory=float(util.memory) if use_util else None,
            temperature_c=float(temp) if temp is not None else None,
            power_w=(power_mw / 1000.0) if power_mw is not None else None,
            power_limit_w=(limit_mw / 1000.0) if limit_mw is not None else None,
            memory_used_bytes=int(mem.used) if have_mem else None,
            memory_total_bytes=int(mem.total) if have_mem else None,
            clock_mhz=float(clock) if clock is not None else None,
            clock_memory_mhz=float(mem_clock) if mem_clock is not None else None,
            clock_max_mhz=float(max_clock) if max_clock is not None else None,
            clock_memory_max_mhz=float(mem_max_clock) if mem_max_clock is not None else None,
            fan_percent=fan,
            pcie_link=link,
            driver_version=self._driver,
        )
