"""Shared vocabulary for sensor providers.

A provider exposes a set of :class:`Capability` values.  A capability is either
*available* (and then every sample carries a real number) or *unavailable* with a
human-readable reason.  Consumers only ever look at values, never at reasons, so a
provider is free to fail in a way that degrades to ``N/A``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class Capability(Enum):
    """A single measurable thing a provider may supply."""

    CPU_NAME = "cpu.name"
    CPU_USAGE = "cpu.usage"
    CPU_PER_CORE = "cpu.per_core"
    CPU_FREQUENCY = "cpu.frequency"
    CPU_TEMPERATURE = "cpu.temperature"
    CPU_POWER = "cpu.power"
    MEMORY = "memory"
    GPU = "gpu"
    FPS = "fps"


class Status(Enum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True)
class CapabilityState:
    status: Status
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is Status.OK

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return self.detail or self.status.value


OK = CapabilityState(Status.OK)
UNAVAILABLE = CapabilityState(Status.UNAVAILABLE)


def unavailable(reason: str) -> CapabilityState:
    return CapabilityState(Status.UNAVAILABLE, reason)


def error(reason: str) -> CapabilityState:
    return CapabilityState(Status.ERROR, reason)


@runtime_checkable
class Provider(Protocol):
    """Minimal contract every source implements."""

    name: str

    def capabilities(self) -> dict:
        """Map of :class:`Capability` -> :class:`CapabilityState`."""

    def start(self) -> None:
        """Acquire OS resources.  Must be idempotent."""

    def stop(self) -> None:
        """Release OS resources.  Must be idempotent and never raise."""

    def sample(self):
        """Return a data object, or ``None`` if this tick yielded nothing new."""
