"""Sensor sampling on a worker thread.

Keeps the window responsive: PDH and NVML calls are fast, but the optional
LibreHardwareMonitor provider can take seconds to initialise its driver, and that
must never block the event loop.  The thread owns the :class:`~sysmon.collector.Collector`
exclusively - it is started and stopped on this thread, which is also what those
Windows APIs expect.
"""

from __future__ import annotations

import threading
from typing import Optional

from PySide6 import QtCore

from ..collector import Collector

MIN_INTERVAL = 0.2
MAX_INTERVAL = 10.0


class SamplerThread(QtCore.QThread):
    """Emits :class:`Snapshot` objects on a fixed cadence."""

    snapshotReady = QtCore.Signal(object)
    notesReady = QtCore.Signal(list)
    failed = QtCore.Signal(str)

    def __init__(self, interval: float = 1.0, enable_cpu: bool = True,
                 enable_memory: bool = True, enable_gpu: bool = True,
                 enable_thermal: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._interval = max(MIN_INTERVAL, min(MAX_INTERVAL, interval))
        self._enable = (enable_cpu, enable_memory, enable_gpu, enable_thermal)
        self._stop = threading.Event()
        self._collector: Optional[Collector] = None
        self._ready = False

    # ------------------------------------------------------------- controls

    @property
    def interval(self) -> float:
        return self._interval

    def set_interval(self, seconds: float) -> None:
        self._interval = max(MIN_INTERVAL, min(MAX_INTERVAL, float(seconds)))

    def stop(self, timeout_ms: int = 4000) -> None:
        self._stop.set()
        if self.isRunning():
            self.wait(timeout_ms)

    # ----------------------------------------------------------------- loop

    def run(self) -> None:  # noqa: D102 - QThread entry point
        cpu, memory, gpu, thermal = self._enable
        collector = Collector(enable_cpu=cpu, enable_memory=memory,
                              enable_gpu=gpu, enable_thermal=thermal)
        self._collector = collector
        try:
            collector.start()
            # PDH rate counters need real elapsed time before the first reading.
            collector.warm(2, pause=0.35)
            self.notesReady.emit(list(collector.notes()))
            first = collector.sample()
            if first is not None:
                self.snapshotReady.emit(first)
            self._ready = True

            while not self._stop.is_set():
                if self._stop.wait(self._interval):
                    break
                try:
                    snap = collector.sample()
                except Exception as exc:
                    self.failed.emit(f"sampling error: {exc}")
                    continue
                if snap is not None:
                    self.snapshotReady.emit(snap)
        except Exception as exc:  # pragma: no cover - defensive
            self.failed.emit(str(exc))
        finally:
            try:
                collector.stop()
            except Exception:
                pass
            self._collector = None
