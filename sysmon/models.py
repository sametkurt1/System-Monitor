"""Immutable snapshot data model.

Every field is optional on purpose: a sensor that cannot be read on this machine is
represented by ``None`` and rendered as ``N/A``.  Nothing is ever guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence


@dataclass(frozen=True)
class CpuCore:
    """One logical processor."""

    index: int
    usage: Optional[float] = None  # percent, 0..100+
    frequency_mhz: Optional[float] = None


@dataclass(frozen=True)
class CpuSnapshot:
    name: str = "Unknown CPU"
    vendor: Optional[str] = None
    usage: Optional[float] = None  # percent
    cores_physical: Optional[int] = None
    cores_logical: Optional[int] = None
    frequency_mhz: Optional[float] = None  # current, real-clock only
    frequency_max_mhz: Optional[float] = None  # max / boost
    frequency_is_nominal: bool = False  # True => value is the base clock, not a live reading
    base_clock_mhz: Optional[float] = None
    temperature_c: Optional[float] = None
    power_w: Optional[float] = None
    per_core: Sequence[CpuCore] = field(default_factory=tuple)

    @property
    def has_frequency(self) -> bool:
        return self.frequency_mhz is not None and not self.frequency_is_nominal

    @property
    def has_temperature(self) -> bool:
        return self.temperature_c is not None

    @property
    def has_power(self) -> bool:
        return self.power_w is not None


@dataclass(frozen=True)
class MemorySnapshot:
    total_bytes: Optional[int] = None
    used_bytes: Optional[int] = None
    available_bytes: Optional[int] = None
    usage: Optional[float] = None  # percent
    committed_bytes: Optional[int] = None
    commit_limit_bytes: Optional[int] = None
    cached_bytes: Optional[int] = None
    standby_bytes: Optional[int] = None

    @property
    def commit_usage(self) -> Optional[float]:
        if self.committed_bytes is None or not self.commit_limit_bytes:
            return None
        return 100.0 * self.committed_bytes / self.commit_limit_bytes


@dataclass(frozen=True)
class GpuSnapshot:
    index: int = 0
    name: str = "Unknown GPU"
    usage: Optional[float] = None
    usage_memory: Optional[float] = None
    temperature_c: Optional[float] = None
    power_w: Optional[float] = None
    power_limit_w: Optional[float] = None
    memory_used_bytes: Optional[int] = None
    memory_total_bytes: Optional[int] = None
    clock_mhz: Optional[float] = None  # graphics / core clock
    clock_memory_mhz: Optional[float] = None
    clock_max_mhz: Optional[float] = None
    clock_memory_max_mhz: Optional[float] = None
    fan_percent: Optional[int] = None
    pcie_link: Optional[str] = None
    driver_version: Optional[str] = None

    @property
    def memory_usage(self) -> Optional[float]:
        if self.memory_used_bytes is None or not self.memory_total_bytes:
            return None
        return 100.0 * self.memory_used_bytes / self.memory_total_bytes


@dataclass(frozen=True)
class FpsSnapshot:
    """Live in-game frame rate and frametime metrics."""

    fps: Optional[float] = None
    frametime_ms: Optional[float] = None
    fps_1percent_low: Optional[float] = None
    app_name: Optional[str] = None
    pid: Optional[int] = None
    is_active: bool = False
    is_available: bool = False
    detail: str = ""

    @property
    def has_fps(self) -> bool:
        return self.fps is not None and self.is_active


@dataclass(frozen=True)
class Snapshot:
    """One complete sample of every monitored category."""

    monotonic: float = 0.0
    cpu: CpuSnapshot = field(default_factory=CpuSnapshot)
    memory: MemorySnapshot = field(default_factory=MemorySnapshot)
    gpus: Sequence[GpuSnapshot] = field(default_factory=tuple)
    fps: FpsSnapshot = field(default_factory=FpsSnapshot)
