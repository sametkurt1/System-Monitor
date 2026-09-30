"""Sensor availability report - the honest answer to "why is this N/A?"."""

from __future__ import annotations

import sys
from typing import List

from .sensors import CapabilityState, Status
from .sensors.thermal import find_assembly, is_elevated

_GLYPH = {Status.OK: "OK", Status.UNAVAILABLE: "--", Status.ERROR: "!!"}


def _mark(state: CapabilityState) -> str:
    return _GLYPH.get(state.status, "??")


def report(collector=None, width: int = 100) -> List[str]:
    from .sensors import CpuSource, MemorySource, NvidiaSource, ThermalPowerSource

    out: List[str] = []
    out.append("=" * min(width, 78))
    out.append(" sysmon sensor diagnostics")
    out.append("=" * min(width, 78))
    out.append(f" python      {sys.version.split()[0]}  ({sys.executable})")
    out.append(f" platform    {sys.platform}")
    out.append(f" elevated    {'yes' if is_elevated() else 'no'}"
               + ("" if is_elevated() else "   (needed for CPU temperature / power)"))
    asm = find_assembly()
    out.append(f" LHM dll     {asm or 'not installed (optional)'}")
    out.append("")

    providers = [p for p in (
        collector.cpu if collector else CpuSource(),
        collector.memory if collector else MemorySource(),
        collector.gpu if collector else NvidiaSource(),
        collector.thermal if collector else ThermalPowerSource(),
    ) if p is not None]

    for prov in providers:
        out.append(f" provider: {prov.name}")
        # Some providers can report which of several strategies is live.
        status = getattr(prov, "status_line", None)
        if callable(status):
            try:
                detail = status()
                if detail:
                    out.append(f"   active path: {detail}")
            except Exception:
                pass
        try:
            caps = prov.capabilities()
        except Exception as exc:
            out.append(f"   !! capability probe failed: {exc}")
            continue
        if not caps:
            out.append("   (no capabilities)")
        for cap, state in caps.items():
            out.append(f"   [{_mark(state)}] {cap.value:<24} {state.detail or state.status.value}")
        out.append("")

    out.append(" Notes")
    out.append("   CPU temperature and package power have no supported Windows API.")
    out.append("   They come from LibreHardwareMonitor (a kernel driver), which needs an")
    out.append("   elevated process. Without it these fields are reported as N/A.")
    out.append("   CPU live frequency is reported only when a real source is available;")
    out.append("   PDH's 'Processor Frequency' is the nominal base clock on most CPUs.")
    out.append("")
    return out
