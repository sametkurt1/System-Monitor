"""Sensor providers for CPU, memory and NVIDIA GPU.

Each provider follows the :class:`~sysmon.sensors.base.Provider` protocol.  Providers
never raise from :meth:`sample`; a sensor that cannot be read yields ``None``, which the
UI renders as ``N/A``.
"""

from .base import Capability, CapabilityState, Provider, Status
from .cpu import CpuSource
from .gpu import NvidiaSource
from .memory import MemorySource
from .thermal import ThermalPowerSource

__all__ = [
    "Capability",
    "CapabilityState",
    "CpuSource",
    "NvidiaSource",
    "MemorySource",
    "Provider",
    "Status",
    "ThermalPowerSource",
]
