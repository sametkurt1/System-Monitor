"""Aggregates every provider into a single :class:`~sysmon.models.Snapshot` per tick."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import List

from .models import CpuSnapshot, GpuSnapshot, MemorySnapshot, Snapshot
from .sensors import CpuSource, MemorySource, NvidiaSource, ThermalPowerSource


class Collector:
    """Owns provider lifetimes and merges their samples.

    A provider that raises during :meth:`sample` is disabled and reported, so one bad
    sensor can never take the dashboard down.
    """

    def __init__(self, enable_cpu: bool = True, enable_memory: bool = True,
                 enable_gpu: bool = True, enable_thermal: bool = True) -> None:
        self.cpu = CpuSource() if enable_cpu else None
        self.memory = MemorySource() if enable_memory else None
        self.gpu = NvidiaSource() if enable_gpu else None
        self.thermal = ThermalPowerSource() if enable_thermal else None
        self.problems: List[str] = []
        self._started = False

    # --------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._started:
            return
        for prov in (self.cpu, self.memory, self.gpu, self.thermal):
            if prov is None:
                continue
            try:
                prov.start()
            except Exception as exc:
                self.problems.append(f"{prov.name}: start failed ({exc})")
        self._started = True

    def stop(self) -> None:
        for prov in (self.cpu, self.memory, self.gpu, self.thermal):
            if prov is None:
                continue
            try:
                prov.stop()
            except Exception as exc:
                self.problems.append(f"{prov.name}: stop failed ({exc})")
        self._started = False

    def __enter__(self) -> "Collector":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    # ------------------------------------------------------------------ notes

    def notes(self) -> List[str]:
        """Human-readable notes about metrics that are unavailable, and why."""
        out: List[str] = []
        for prov in (self.cpu, self.memory, self.gpu, self.thermal):
            if prov is None:
                continue
            try:
                states = prov.capabilities()
            except Exception as exc:
                out.append(f"{prov.name}: capability probe failed ({exc})")
                continue
            for cap, state in states.items():
                if not state.ok and state.detail:
                    out.append(f"{prov.name}: {cap.value} unavailable - {state.detail}")
        return out

    # ----------------------------------------------------------------- sample

    def sample(self) -> Snapshot:
        cpu: CpuSnapshot = CpuSnapshot()
        mem: MemorySnapshot = MemorySnapshot()
        gpus: List[GpuSnapshot] = []

        if self.cpu is not None:
            try:
                got = self.cpu.sample()
                if got is not None:
                    cpu = got
            except Exception as exc:
                self.problems.append(f"cpu: {exc}")

        if self.memory is not None:
            try:
                got = self.memory.sample()
                if got is not None:
                    mem = got
            except Exception as exc:
                self.problems.append(f"memory: {exc}")

        if self.gpu is not None:
            try:
                gpus = list(self.gpu.sample() or [])
            except Exception as exc:
                self.problems.append(f"gpu: {exc}")

        # Merge optional temperature / power / real frequency into the CPU snapshot.
        if self.thermal is not None:
            try:
                temp, power, freq, max_freq = self.thermal.sample()
                if any(v is not None for v in (temp, power, freq, max_freq)):
                    cpu = replace(
                        cpu,
                        temperature_c=temp if temp is not None else cpu.temperature_c,
                        power_w=power if power is not None else cpu.power_w,
                        frequency_mhz=freq if freq is not None else cpu.frequency_mhz,
                        frequency_max_mhz=max_freq if max_freq is not None else cpu.frequency_max_mhz,
                        frequency_is_nominal=False if freq is not None else cpu.frequency_is_nominal,
                    )
            except Exception as exc:
                self.problems.append(f"thermal: {exc}")

        return Snapshot(monotonic=time.monotonic(), cpu=cpu, memory=mem, gpus=tuple(gpus))

    def gpus(self) -> List[GpuSnapshot]:
        try:
            return list(self.gpu.sample() or []) if self.gpu is not None else []
        except Exception:
            return []

    def enabled_categories(self) -> set:
        """Which categories have a provider attached."""
        out = set()
        if self.cpu is not None:
            out.add("CPU")
        if self.memory is not None:
            out.add("MEMORY")
        if self.gpu is not None:
            out.add("GPU")
        return out

    def warm(self, rounds: int = 2, pause: float = 0.45) -> None:
        """Run throwaway samples so the first visible frame has real data.

        PDH rate counters only produce a meaningful value once two ``CollectQueryData``
        calls are separated by real elapsed time, hence the pause.
        """
        for _ in range(max(1, rounds)):
            self.sample()
            time.sleep(pause)
