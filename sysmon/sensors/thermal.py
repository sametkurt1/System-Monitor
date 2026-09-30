"""CPU temperature and package power - the two metrics Windows itself does not expose.

There is no supported Windows API for either value.  ``MSAcpi_ThermalZoneTemperature``
is absent on most desktop CPUs (it only exists when the firmware publishes an ACPI
thermal zone, and when present it usually reports the board zone, not the CPU die), and
Windows exposes no CPU energy-meter performance counter.

The only trustworthy source is a ring-0 driver based library.  This module uses
LibreHardwareMonitor when its assembly is available, and otherwise reports the metric as
unavailable with a precise reason.  It never invents a value.

Expectation management: LibreHardwareMonitor needs to load a kernel driver, so real
readings generally require an **elevated** process.  Unprivileged runs fall back to
whatever the driver already exposes, which is often nothing.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
from typing import Dict, Optional, Tuple

from .base import Capability, CapabilityState, OK, unavailable

_ASSEMBLY_NAME = "LibreHardwareMonitorLib.dll"

# Search locations, most specific first.  ``sysmon sensors install`` drops the
# assembly into ``<app>/sensors`` so a frozen build keeps working.
_SEARCH_SUBDIRS = ("sensors", "lib", ".")

# Variants in preference order.  The netstandard2.0 build is the most portable
# under pythonnet; the net8.0 build fails to resolve System.PlatformID on some
# hosts, and net472 wants a different set of shims.
_VARIANTS = ("netstandard2.0", "net8.0", "net472")


def _candidate_paths() -> list:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    root = os.path.dirname(here)
    roots = [here, root]
    if getattr(sys, "frozen", False):
        roots.insert(0, os.path.dirname(os.path.abspath(sys.executable)))
    seen = set()
    out = []
    for base in roots:
        for sub in _SEARCH_SUBDIRS:
            d = os.path.normpath(os.path.join(base, sub))
            if d in seen:
                continue
            seen.add(d)
            out.append(os.path.join(d, _ASSEMBLY_NAME))
    return out


def find_assembly() -> Optional[str]:
    for path in _candidate_paths():
        if os.path.isfile(path):
            return path
    return None


def _dotnet_major_versions() -> list:
    """Major versions of the .NET runtimes installed on this machine."""
    try:
        import glob
        import re
        root = os.environ.get("ProgramFiles", r"C:\Program Files")
        found = []
        for path in glob.glob(os.path.join(root, "dotnet", "shared", "Microsoft.NETCore.App", "*")):
            m = re.search(r"(\d+)\.(\d+)\.(\d+)$", os.path.basename(path))
            if m:
                found.append(int(m.group(1)))
        return sorted(set(found), reverse=True)
    except Exception:
        return []


def is_elevated() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def get_ipc_temp_path() -> str:
    """Path to the IPC file used by the elevated thermal worker."""
    import tempfile
    username = os.environ.get("USERNAME", "user")
    return os.path.join(tempfile.gettempdir(), f"sysmon_thermal_{username}.dat")


def read_ipc_temperature(max_age_s: float = 3.5) -> Optional[float]:
    """Read the latest temperature written by the elevated background worker."""
    path = get_ipc_temp_path()
    try:
        if not os.path.isfile(path):
            return None
        import struct
        with open(path, "rb") as f:
            data = f.read(16)
        if len(data) < 16:
            return None
        ts, temp = struct.unpack("dd", data)
        import time
        if time.time() - ts <= max_age_s and 0.0 < temp < 150.0:
            return float(temp)
    except Exception:
        pass
    return None


def run_thermal_worker(parent_pid: int) -> None:
    """Elevated worker process: samples LibreHardwareMonitor and writes temperature to IPC file.

    Monitors parent_pid; when the parent GUI closes (or crashes), the worker exits cleanly.
    """
    import struct
    import time

    h_parent = None
    kernel32 = None
    if sys.platform == "win32":
        try:
            import ctypes
            SYNCHRONIZE = 0x00100000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            h_parent = kernel32.OpenProcess(SYNCHRONIZE, False, int(parent_pid))
        except Exception:
            h_parent = None

    source = ThermalPowerSource()
    source.start()

    ipc_path = get_ipc_temp_path()
    tmp_path = ipc_path + f".{os.getpid()}.tmp"

    try:
        while True:
            # Check if parent is still alive
            if kernel32 and h_parent:
                wait_res = kernel32.WaitForSingleObject(h_parent, 800)
                # WAIT_OBJECT_0 (0) means the parent has exited!
                if wait_res == 0:
                    break
            else:
                time.sleep(0.8)

            temp, power, freq, max_freq = source.sample()
            if temp is not None:
                try:
                    with open(tmp_path, "wb") as f:
                        f.write(struct.pack("dd", time.time(), float(temp)))
                    os.replace(tmp_path, ipc_path)
                except Exception:
                    pass
    finally:
        source.stop()
        if kernel32 and h_parent:
            try:
                kernel32.CloseHandle(h_parent)
            except Exception:
                pass
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        try:
            if os.path.isfile(ipc_path):
                os.remove(ipc_path)
        except Exception:
            pass


def start_thermal_worker_elevated(parent_pid: int) -> Tuple[bool, str]:
    """Launch the elevated thermal worker via Windows UAC without closing the current app."""
    if sys.platform != "win32":
        return False, "Not supported on this platform"

    try:
        import ctypes
        import ctypes.wintypes as w

        class SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize", w.DWORD),
                ("fMask", w.ULONG),
                ("hwnd", w.HWND),
                ("lpVerb", w.LPCWSTR),
                ("lpFile", w.LPCWSTR),
                ("lpParameters", w.LPCWSTR),
                ("lpDirectory", w.LPCWSTR),
                ("nShow", ctypes.c_int),
                ("hInstApp", w.HINSTANCE),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", w.LPCWSTR),
                ("hkeyClass", w.HKEY),
                ("dwHotKey", w.DWORD),
                ("hIconOrMonitor", w.HANDLE),
                ("hProcess", w.HANDLE),
            ]

        SW_HIDE = 0
        repo_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        gui_script = os.path.join(repo_dir, "sysmon.py")

        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        target_py = pythonw if os.path.exists(pythonw) else sys.executable

        if getattr(sys, "frozen", False):
            lp_file = sys.executable
            lp_params = f"--thermal-worker {parent_pid}"
        elif os.path.isfile(gui_script):
            lp_file = target_py
            lp_params = f'"{gui_script}" --thermal-worker {parent_pid}'
        else:
            lp_file = target_py
            code = (
                f"import sys; sys.path.insert(0, {repr(repo_dir)});"
                f"from sysmon.sensors.thermal import run_thermal_worker; run_thermal_worker({parent_pid})"
            )
            lp_params = f'-c "{code}"'

        sei = SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
        sei.fMask = 0
        sei.hwnd = None
        sei.lpVerb = "runas"
        sei.lpFile = lp_file
        sei.lpParameters = lp_params
        sei.lpDirectory = repo_dir
        sei.nShow = SW_HIDE

        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        if not shell32.ShellExecuteExW(ctypes.byref(sei)):
            err = kernel32.GetLastError()
            if err == 1223:  # ERROR_CANCELLED (user clicked No on UAC)
                return False, "Administrator permission cancelled by user"
            return False, f"UAC elevation failed (error {err})"

        return True, "Elevated thermal service launched"
    except Exception as exc:
        return False, f"Elevation error: {exc}"


class PdhEnergyMeter:
    """Windows RAPL Energy Meter reader via PDH.

    Provides CPU package power in Watts across Windows 10/11 systems without
    requiring administrator privileges or third-party kernel drivers.
    """

    def __init__(self) -> None:
        self._query = None
        self._counter = None
        self._ready = False
        if sys.platform != "win32":
            return
        try:
            import ctypes
            self._pdh = ctypes.WinDLL("pdh.dll")
            q = ctypes.c_size_t()
            if self._pdh.PdhOpenQueryW(None, None, ctypes.byref(q)) != 0:
                return
            self._query = q
            c = ctypes.c_size_t()
            if self._pdh.PdhAddEnglishCounterW(q, r"\Energy Meter(*)\Power", None, ctypes.byref(c)) != 0:
                self._pdh.PdhCloseQuery(q)
                self._query = None
                return
            self._counter = c
            self._ready = True
            self._pdh.PdhCollectQueryData(self._query)
        except Exception:
            self._ready = False

    def sample(self) -> Optional[float]:
        if not self._ready or self._query is None:
            return None
        try:
            import ctypes
            import ctypes.wintypes as w

            class FMT(ctypes.Structure):
                class U(ctypes.Union):
                    _fields_ = [("doubleValue", ctypes.c_double), ("largeValue", ctypes.c_longlong)]
                _fields_ = [("CStatus", w.DWORD), ("value", U)]

            class ITEM(ctypes.Structure):
                _fields_ = [("szName", w.LPWSTR), ("FmtValue", FMT)]

            if self._pdh.PdhCollectQueryData(self._query) != 0:
                return None
            buf_size = w.DWORD(0)
            item_count = w.DWORD(0)
            self._pdh.PdhGetFormattedCounterArrayW(self._counter, 0x200, ctypes.byref(buf_size), ctypes.byref(item_count), None)
            if buf_size.value == 0:
                return None
            buf = (ctypes.c_byte * buf_size.value)()
            if self._pdh.PdhGetFormattedCounterArrayW(self._counter, 0x200, ctypes.byref(buf_size), ctypes.byref(item_count), buf) != 0:
                return None

            items = ctypes.cast(buf, ctypes.POINTER(ITEM))
            pkg_val = None
            total_cores = 0.0
            for i in range(item_count.value):
                name = str(items[i].szName)
                v = items[i].FmtValue.value.doubleValue
                if v <= 0:
                    continue
                if name.endswith("_PKG") or "_PKG" in name:
                    pkg_val = v
                    break
                elif "CORE" in name or "Core" in name:
                    total_cores += v

            res = pkg_val if pkg_val is not None else (total_cores if total_cores > 0 else None)
            return (res / 1000.0) if res is not None else None
        except Exception:
            return None

    def stop(self) -> None:
        if self._query is not None:
            try:
                self._pdh.PdhCloseQuery(self._query)
            except Exception:
                pass
            self._query = None
            self._ready = False


class ThermalPowerSource:
    """LibreHardwareMonitor-backed CPU package temperature and power, with PDH RAPL fallback."""

    name = "librehardwaremonitor"

    def __init__(self) -> None:
        self._computer = None
        self._ready = False
        self._reason = "not started"
        self._states: Dict[Capability, CapabilityState] = {}
        self._last_temp: Optional[float] = None
        self._last_power: Optional[float] = None
        self._last_freq: Optional[float] = None
        self._last_max_freq: Optional[float] = None
        self._energy_meter = PdhEnergyMeter()

    # ----------------------------------------------------------- capabilities

    def capabilities(self) -> Dict[Capability, CapabilityState]:
        has_temp = (self._ready and is_elevated()) or (read_ipc_temperature() is not None)
        temp_state = OK if has_temp else unavailable(self._reason)
        power_state = OK if (self._ready or self._energy_meter._ready) else unavailable(self._reason)
        self._states = {Capability.CPU_TEMPERATURE: temp_state,
                        Capability.CPU_POWER: power_state}
        return self._states

    def status_line(self) -> str:
        if self._ready and is_elevated():
            return "active (LibreHardwareMonitor)"
        if read_ipc_temperature() is not None:
            return "active (LibreHardwareMonitor via elevated background worker)"
        if self._energy_meter._ready:
            if not is_elevated():
                return "active (Windows Energy Meter power; elevate for CPU temperature)"
            return "active (Windows Energy Meter power)"
        return self._reason

    # --------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._ready or sys.platform != "win32":
            return

        dll = find_assembly()
        if not dll:
            self._reason = (
                "LibreHardwareMonitor assembly not found - run "
                "`sysmon sensors install` to enable CPU temperature and power"
            )
            return

        try:
            importlib.import_module("clr")
            System = importlib.import_module("System")
        except Exception as exc:
            self._reason = f"pythonnet not available ({_first_line(exc)}) - `pip install pythonnet`"
            return

        try:
            asm = System.Reflection.Assembly.LoadFrom(dll)
        except Exception as exc:
            self._reason = f"could not load {os.path.basename(dll)} ({_first_line(exc)})"
            return

        ctype = None
        try:
            ctype = asm.GetType("LibreHardwareMonitor.Hardware.Computer")
        except Exception as exc:
            self._reason = f"unexpected assembly layout ({_first_line(exc)})"
            return
        if ctype is None:
            self._reason = "unexpected assembly layout (Computer type not found)"
            return

        try:
            computer = System.Activator.CreateInstance(ctype)
        except Exception as exc:
            self._reason = f"could not create Computer ({_first_line(exc)})"
            return

        try:
            computer.IsCpuEnabled = True
            computer.IsMotherboardEnabled = True
            for prop in ("IsGpuEnabled", "IsMemoryEnabled",
                         "IsStorageEnabled", "IsNetworkEnabled", "IsControllerEnabled"):
                try:
                    setattr(computer, prop, False)
                except Exception:
                    pass
            computer.Open()
        except Exception as exc:
            self._reason = self._explain(exc)
            return

        self._computer = computer
        self._ready = True
        self._reason = "active (LibreHardwareMonitor)"
        self._states = {Capability.CPU_TEMPERATURE: OK if is_elevated() else unavailable("run as administrator for temperature"),
                        Capability.CPU_POWER: OK}

    # CLR exception types that all mean "the assembly could not be resolved".
    _LOAD_FAILURES = frozenset({
        "FileNotFoundException", "FileLoadException", "BadImageFormatException",
        "TypeLoadException", "TypeInitializationException", "MissingMethodException",
        "MissingFieldException", "FileLoadException",
    })

    def _explain(self, exc: BaseException) -> str:
        text = _first_line(exc)
        kind = type(exc).__name__
        parts: list = []

        if kind in self._LOAD_FAILURES:
            parts.append(f"CLR assembly-load failure ({kind})")
        if kind == "TypeInitializationException":
            parts.append("CLR assembly-load failure (TypeInitializationException)")

        for match in re.finditer(r"Version=(\d+)\.(\d+)\.(\d+)\.(\d+)", text):
            parts.append(f"needs a CLR assembly targeting .NET {match.group(1)}")
            break

        if not is_elevated():
            parts.append("run as administrator")
        else:
            parts.append("the kernel driver may be blocked by Defender or the "
                         "Windows vulnerable-driver blocklist")
        if not parts:
            parts.append(text)
        else:
            parts.append(f"({text})")
        return "; ".join(parts)

    def stop(self) -> None:
        if self._computer is not None:
            try:
                self._computer.Close()
            except Exception:
                pass
        self._computer = None
        self._ready = False
        self._energy_meter.stop()

    # ----------------------------------------------------------------- sample

    def sample(self) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """Return ``(temperature_c, power_w, current_mhz, max_mhz)``."""
        import math

        temp = power = freq = max_freq = None

        if self._ready and self._computer is not None:
            try:
                def scan_hardware(h):
                    nonlocal temp, power, freq, max_freq
                    try:
                        h.Update()
                    except Exception:
                        pass
                    for s in h.Sensors:
                        if s.Value is None:
                            continue
                        try:
                            val = float(s.Value)
                        except (TypeError, ValueError):
                            continue
                        if math.isnan(val) or val <= 0.0:
                            continue
                        stype = str(s.SensorType)
                        sname = str(s.Name)

                        if stype == "Temperature":
                            if 0.0 < val < 150.0:
                                if any(k in sname for k in ("Tctl", "Package", "Total", "Average", "Core Max")):
                                    temp = val
                                elif temp is None:
                                    temp = val
                        elif stype == "Power":
                            if 0.0 < val < 1000.0:
                                if any(k in sname for k in ("Package", "Total", "CPU")):
                                    power = val
                                elif power is None:
                                    power = val
                        elif stype == "Clock":
                            if 100.0 < val < 10000.0:
                                if max_freq is None or val > max_freq:
                                    max_freq = val
                                if freq is None:
                                    freq = val

                    for sub in h.SubHardware:
                        scan_hardware(sub)

                for h in self._computer.Hardware:
                    htype = str(h.HardwareType)
                    if htype in ("Cpu", "Motherboard"):
                        scan_hardware(h)
            except Exception:
                pass

        # If power wasn't reported by LHM (e.g. un-elevated run), use native Windows RAPL Energy Meter
        if power is None:
            power = self._energy_meter.sample()

        # If in-process temp is None (e.g. running un-elevated), check for elevated thermal worker IPC
        if temp is None:
            ipc_temp = read_ipc_temperature()
            if ipc_temp is not None:
                temp = ipc_temp

        if temp is not None:
            self._last_temp = temp
        if power is not None:
            self._last_power = power
        if freq is not None:
            self._last_freq = freq
        if max_freq is not None:
            self._last_max_freq = max_freq

        return (
            temp if temp is not None else self._last_temp,
            power if power is not None else self._last_power,
            freq if freq is not None else self._last_freq,
            max_freq if max_freq is not None else self._last_max_freq,
        )



def _first_line(exc: BaseException, limit: int = 160) -> str:
    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    return text.splitlines()[0][:limit]
