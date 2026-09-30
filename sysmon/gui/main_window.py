"""The sysmon window.

Layout is a three-column top row (CPU, GPU, Memory) with a full-width per-core grid
underneath.  Everything is driven by :class:`~sysmon.gui.sampler.SamplerThread`, so a
slow sensor can never freeze the UI, and a 60 fps animation timer eases the gauges
between samples so 1 Hz updates read as motion rather than jumps.
"""

from __future__ import annotations

import time
from collections import deque
from typing import List

from PySide6 import QtCore, QtGui, QtWidgets

from ..formatting import human_freq, human_gb, human_temp, human_watt
from ..models import Snapshot
from . import theme as T
from .sampler import SamplerThread
from .widgets import (BarGauge, Card, CoreGrid, ElidedLabel, Gauge, Sparkline,
                      StatRow, make_icon)

HISTORY = 120
INTERVALS = (0.5, 1.0, 2.0, 5.0)


def _tooltip_for(reason: str, fallback: str) -> str:
    return reason or fallback


class _CleanerThread(QtCore.QThread):
    finishedClean = QtCore.Signal(object)

    def run(self) -> None:
        from ..cleaner import clean_ram_with_elevation
        result = clean_ram_with_elevation()
        self.finishedClean.emit(result)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, interval: float = 1.0, enable_cpu: bool = True,
                 enable_memory: bool = True, enable_gpu: bool = True,
                 enable_thermal: bool = True, show_cores: bool = True) -> None:
        super().__init__()
        self.setWindowTitle("sysmon")
        self.setWindowIcon(make_icon())
        self.setMinimumSize(940, 400)
        self.resize(1120, 440)

        self._history: dict = {
            "cpu": deque(maxlen=HISTORY),
            "mem": deque(maxlen=HISTORY),
            "gpu": deque(maxlen=HISTORY),
        }
        self._last = Snapshot()
        self._paused = False
        self._top_most = False
        self._notes: List[str] = []
        self._show_cores = show_cores
        self._enabled = {"CPU": enable_cpu, "MEMORY": enable_memory, "GPU": enable_gpu}
        self.cores_card = None
        self.core_grid = CoreGrid()

        self._build_ui()
        self._start_sampler(interval, enable_cpu, enable_memory, enable_gpu, enable_thermal)

        self._anim = QtCore.QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._on_anim)
        self._anim.start()

        self._clock = QtCore.QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._refresh_clock)
        self._clock.start()

    # ------------------------------------------------------------------ ui

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        central.setStyleSheet(f"background: {T.BG};")
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)
        root.addLayout(self._build_header())

        grid = QtWidgets.QGridLayout()
        grid.setSpacing(12)
        self._build_cpu_card()
        self._build_gpu_card()
        self._build_memory_card()
        self._build_cores_card()

        # Three cards in one row: CPU, GPU, Memory.
        grid.addWidget(self.cpu_card, 0, 0)
        grid.addWidget(self.gpu_card, 0, 1)
        grid.addWidget(self.mem_card, 0, 2)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 5)
        grid.setColumnStretch(2, 4)
        grid.setRowStretch(0, 1)
        root.addLayout(grid, 1)
        root.addLayout(self._build_statusbar())
        self.setCentralWidget(central)

    def _build_header(self) -> QtWidgets.QHBoxLayout:
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(10)

        mark = QtWidgets.QLabel()
        mark.setPixmap(make_icon(96).pixmap(20, 20))
        mark.setFixedSize(20, 20)
        bar.addWidget(mark)

        titles = QtWidgets.QVBoxLayout()
        titles.setSpacing(1)
        name = QtWidgets.QLabel("sysmon")
        name.setFont(T.font(14.0, QtGui.QFont.Weight.Bold))
        name.setStyleSheet(f"color: {T.FG_TITLE}; letter-spacing: 0.5px;")
        self.subtitle = QtWidgets.QLabel("starting sensors\u2026")
        self.subtitle.setFont(T.font(10.0))
        self.subtitle.setStyleSheet(f"color: {T.FG_DIM};")
        titles.addWidget(name)
        titles.addWidget(self.subtitle)
        bar.addLayout(titles)
        bar.addStretch(1)

        self.clock = QtWidgets.QLabel("--:--:--")
        self.clock.setFont(T.font(11.5, QtGui.QFont.Weight.DemiBold, family=T.NUM_FAMILY, tabular=True))
        self.clock.setStyleSheet(f"color: {T.FG_MUTED}; margin-right: 6px;")
        bar.addWidget(self.clock)
        self._refresh_clock()

        self.interval_box = QtWidgets.QComboBox()
        for value in INTERVALS:
            self.interval_box.addItem(f"{value:g}s", value)
        self.interval_box.setCurrentIndex(1)
        self.interval_box.setToolTip("Refresh interval")
        self.interval_box.currentIndexChanged.connect(self._on_interval_changed)
        bar.addWidget(self.interval_box)

        self.clean_btn = QtWidgets.QPushButton("Clean RAM")
        self.clean_btn.setToolTip("Purge standby list & system file cache (frees memory from closed apps)")
        self.clean_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.clean_btn.setFixedHeight(27)
        self.clean_btn.setStyleSheet(self._button_qss())
        self.clean_btn.clicked.connect(self._on_clean_ram)
        bar.addWidget(self.clean_btn)

        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.pause_btn.setFixedHeight(27)
        self.pause_btn.setStyleSheet(self._button_qss())
        self.pause_btn.toggled.connect(self._on_pause)
        bar.addWidget(self.pause_btn)

        self.top_btn = QtWidgets.QPushButton("Top")
        self.top_btn.setCheckable(True)
        self.top_btn.setToolTip("Keep the window above other windows")
        self.top_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.top_btn.setFixedHeight(27)
        self.top_btn.setStyleSheet(self._button_qss())
        self.top_btn.toggled.connect(self._on_top)
        bar.addWidget(self.top_btn)

        from ..cleaner import is_admin
        if not is_admin():
            self.admin_btn = QtWidgets.QPushButton("🛡️ Admin")
            self.admin_btn.setToolTip("Restart sysmon as Administrator to enable CPU temperature sensors")
            self.admin_btn.setCursor(QtCore.Qt.PointingHandCursor)
            self.admin_btn.setFixedHeight(27)
            self.admin_btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.alpha_css(T.qcolor(T.ACCENT, 20))};
                border: 1px solid {T.alpha_css(T.qcolor(T.ACCENT, 90))};
                border-radius: 6px;
                color: {T.ACCENT};
                padding: 4px 10px;
                font-size: 11.5px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: {T.alpha_css(T.qcolor(T.ACCENT, 45))};
                border-color: {T.ACCENT};
                color: {T.FG_TITLE};
            }}
            """)
            self.admin_btn.clicked.connect(self._on_run_as_admin)
            bar.addWidget(self.admin_btn)
        else:
            self.admin_badge = QtWidgets.QLabel("🛡️ Admin")
            self.admin_badge.setToolTip("Running with Administrator privileges (full sensor & cache access)")
            self.admin_badge.setFixedHeight(27)
            self.admin_badge.setAlignment(QtCore.Qt.AlignCenter)
            self.admin_badge.setStyleSheet(f"""
            QLabel {{
                background: {T.alpha_css(T.qcolor(T.ONLINE, 18))};
                border: 1px solid {T.alpha_css(T.qcolor(T.ONLINE, 65))};
                border-radius: 6px;
                color: {T.ONLINE};
                padding: 2px 9px;
                font-size: 11px;
                font-weight: 600;
            }}
            """)
            bar.addWidget(self.admin_badge)

        info = QtWidgets.QPushButton("i")
        info.setToolTip("Sensor diagnostics")
        info.setCursor(QtCore.Qt.PointingHandCursor)
        info.setFixedSize(27, 27)
        info.setStyleSheet(self._button_qss())
        info.clicked.connect(self._show_diagnostics)
        bar.addWidget(info)
        return bar

    def _button_qss(self) -> str:
        return f"""
        QPushButton {{
            background: {T.INPUT};
            border: 1px solid {T.CARD_BORDER};
            border-radius: 6px;
            color: {T.FG_MUTED};
            padding: 4px 11px;
            font-size: 11.5px;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background: {T.CARD_TOP};
            border-color: {T.CARD_BORDER_HI};
            color: {T.FG_TITLE};
        }}
        QPushButton:checked {{
            background: {T.alpha_css(T.qcolor(T.ACCENT, 25))};
            border-color: {T.alpha_css(T.qcolor(T.ACCENT, 160))};
            color: {T.ACCENT};
            font-weight: 600;
        }}
        """

    # ------------------------------------------------------------- cards

    def _build_cpu_card(self) -> None:
        card = Card("CPU", T.ACCENT)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(14)
        self.cpu_gauge = Gauge(T.ACCENT, "%", "CPU")
        row.addWidget(self.cpu_gauge, 0)
        stats = QtWidgets.QVBoxLayout()
        stats.setSpacing(3)
        self.cpu_name = ElidedLabel("\u2014")
        self.cpu_name.setFont(T.font(11.0, QtGui.QFont.Weight.DemiBold))
        self.cpu_name.setStyleSheet(f"color: {T.FG_TITLE}; margin-bottom: 3px;")
        stats.addWidget(self.cpu_name)
        stats.addSpacing(3)
        self.cpu_freq = StatRow("Frequency")
        self.cpu_max = StatRow("Max / Boost")
        self.cpu_temp = StatRow("Temperature")
        self.cpu_temp.setCursor(QtCore.Qt.PointingHandCursor)
        self.cpu_temp.mousePressEvent = lambda e: self._on_run_as_admin()
        self.cpu_power = StatRow("Power")
        for row_widget in (self.cpu_freq, self.cpu_max, self.cpu_temp, self.cpu_power):
            stats.addWidget(row_widget)
        stats.addStretch(1)
        row.addLayout(stats, 1)
        card.body.addLayout(row, 1)
        self.cpu_spark = Sparkline(T.ACCENT, 38)
        card.body.addWidget(self.cpu_spark)
        self.cpu_card = card

    def _build_gpu_card(self) -> None:
        card = Card("GPU", T.GPU)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(14)
        self.gpu_gauge = Gauge(T.GPU, "%", "GPU")
        row.addWidget(self.gpu_gauge, 0)
        stats = QtWidgets.QVBoxLayout()
        stats.setSpacing(3)
        self.gpu_name = ElidedLabel("\u2014")
        self.gpu_name.setFont(T.font(11.0, QtGui.QFont.Weight.DemiBold))
        self.gpu_name.setStyleSheet(f"color: {T.FG_TITLE}; margin-bottom: 3px;")
        stats.addWidget(self.gpu_name)
        stats.addSpacing(3)
        self.gpu_temp = StatRow("Temperature")
        self.gpu_power = StatRow("Power")
        self.gpu_core = StatRow("Core Clock")
        self.gpu_memclk = StatRow("Mem Clock")
        for row_widget in (self.gpu_temp, self.gpu_power, self.gpu_core, self.gpu_memclk):
            stats.addWidget(row_widget)
        stats.addStretch(1)
        row.addLayout(stats, 1)
        card.body.addLayout(row, 1)
        self.gpu_spark = Sparkline(T.GPU, 38)
        card.body.addWidget(self.gpu_spark)
        self.gpu_card = card

    def _build_memory_card(self) -> None:
        card = Card("Memory", T.MEM)
        self.mem_bar = BarGauge("RAM", T.MEM, "%", 22)
        self.vram_bar = BarGauge("VRAM", T.VRAM, "%", 22)
        card.body.addWidget(self.mem_bar)
        card.body.addWidget(self.vram_bar)
        card.body.addSpacing(6)
        self.mem_used = StatRow("Used")
        self.mem_avail = StatRow("Available")
        self.mem_commit = StatRow("Committed")
        self.mem_cache = StatRow("Standby Cache")
        for row_widget in (self.mem_used, self.mem_avail, self.mem_commit, self.mem_cache):
            card.body.addWidget(row_widget)
        card.body.addSpacing(6)

        self.mem_clean_btn = QtWidgets.QPushButton("⚡ Clean Standby Cache")
        self.mem_clean_btn.setToolTip("Purge standby RAM cache left by closed apps without affecting active programs")
        self.mem_clean_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.mem_clean_btn.setFixedHeight(26)
        self.mem_clean_btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.INPUT};
                border: 1px solid {T.CARD_BORDER};
                border-radius: 6px;
                color: {T.alpha_css(T.qcolor(T.MEM, 210))};
                font-size: 11px;
                font-weight: 600;
                padding: 3px 8px;
            }}
            QPushButton:hover {{
                background: {T.CARD_TOP};
                border-color: {T.alpha_css(T.qcolor(T.MEM, 150))};
                color: {T.FG_TITLE};
            }}
            QPushButton:pressed {{
                background: {T.alpha_css(T.qcolor(T.MEM, 35))};
            }}
            QPushButton:disabled {{
                color: {T.FG_DIM};
                border-color: {T.CARD_BORDER};
            }}
        """)
        self.mem_clean_btn.clicked.connect(self._on_clean_ram)
        card.body.addWidget(self.mem_clean_btn)

        card.body.addStretch(1)
        self.mem_spark = Sparkline(T.MEM, 38)
        card.body.addWidget(self.mem_spark)
        self.mem_card = card

    def _build_cores_card(self) -> None:
        self.core_grid = CoreGrid()
        self.cores_card = None

    def _build_statusbar(self) -> QtWidgets.QHBoxLayout:
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(10)
        self.status = QtWidgets.QLabel("")
        self.status.setFont(T.font(10.0))
        self.status.setStyleSheet(f"color: {T.FG_DIM};")
        bar.addWidget(self.status, 1)
        hint = QtWidgets.QLabel("Space pause  \u00b7  C clean RAM  \u00b7  R refresh  \u00b7  Esc quit")
        hint.setFont(T.font(10.0))
        hint.setStyleSheet(f"color: {T.FG_DIM};")
        bar.addWidget(hint)
        return bar

    # ------------------------------------------------------------ sampler

    def _start_sampler(self, interval: float, cpu: bool, memory: bool, gpu: bool,
                       thermal: bool) -> None:
        self.sampler = SamplerThread(interval=interval, enable_cpu=cpu,
                                     enable_memory=memory, enable_gpu=gpu,
                                     enable_thermal=thermal, parent=self)
        self.sampler.snapshotReady.connect(self._on_snapshot)
        self.sampler.notesReady.connect(self._on_notes)
        self.sampler.failed.connect(self._on_failed)
        self.sampler.start()
        # Reflect the requested interval in the combo box.
        for i in range(self.interval_box.count()):
            if abs(self.interval_box.itemData(i) - interval) < 0.01:
                self.interval_box.setCurrentIndex(i)
                break

    # -------------------------------------------------------------- events

    def _on_interval_changed(self, index: int) -> None:
        value = self.interval_box.itemData(index)
        if value:
            self.sampler.set_interval(value)

    def _on_pause(self, checked: bool) -> None:
        self._paused = checked
        # Unpausing requests an immediate sample by nudging the interval to 0.
        if not checked:
            self.sampler.set_interval(self.interval_box.currentData())

    def _on_cores(self, checked: bool) -> None:
        self._show_cores = checked
        if self.cores_card is not None:
            self.cores_card.setVisible(checked)

    def _on_top(self, checked: bool) -> None:
        self._top_most = checked
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, checked)
        self.show()  # re-apply the flag
        self.update()

    def _on_anim(self) -> None:
        for widget in (self.cpu_gauge, self.gpu_gauge, self.mem_bar, self.vram_bar):
            widget.tick()

    def _refresh_clock(self) -> None:
        self.clock.setText(time.strftime("%H:%M:%S"))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (QtCore.Qt.Key.Key_Escape, QtCore.Qt.Key.Key_Q):
            self.close()
        elif key == QtCore.Qt.Key.Key_Space:
            self.pause_btn.toggle()
        elif key == QtCore.Qt.Key.Key_C:
            self._on_clean_ram()
        elif key == QtCore.Qt.Key.Key_R:
            self.sampler.set_interval(0.2)
            QtCore.QTimer.singleShot(1200,
                                    lambda: self.sampler.set_interval(
                                        self.interval_box.currentData()))
        else:
            super().keyPressEvent(event)

    def _on_run_as_admin(self) -> None:
        from ..cleaner import is_admin
        from ..sensors.thermal import read_ipc_temperature, start_thermal_worker_elevated

        if is_admin() or read_ipc_temperature() is not None:
            self.status.setText("✓ CPU temperature sensor is already active.")
            return

        self.status.setText("⚡ Requesting administrator permission for CPU temperature...")
        QtWidgets.QApplication.processEvents()

        success, msg = start_thermal_worker_elevated(os.getpid())
        if success:
            self.status.setText("⚡ Elevated sensor starting...")
            self.sampler.set_interval(0.2)
            QtCore.QTimer.singleShot(1500, lambda: self.sampler.set_interval(self.interval_box.currentData()))
        else:
            self.status.setText(f"⚠️ {msg}")

    def _on_clean_ram(self) -> None:
        if getattr(self, "_cleaning_ram", False):
            return
        self._cleaning_ram = True
        self.clean_btn.setEnabled(False)
        self.clean_btn.setText("Cleaning...")
        self.mem_clean_btn.setEnabled(False)
        self.mem_clean_btn.setText("Cleaning cache...")
        self.status.setText("⚡ Purging standby list & system file cache...")

        self._clean_thread = _CleanerThread(parent=self)
        self._clean_thread.finishedClean.connect(self._on_clean_finished)
        self._clean_thread.start()

    def _on_clean_finished(self, result) -> None:
        self._cleaning_ram = False
        from ..formatting import human_gb

        if result.success:
            freed_str = f"Freed {human_gb(result.freed_bytes)}" if result.freed_bytes > 0 else "Cache clean!"
            self.clean_btn.setText(freed_str)
            self.mem_clean_btn.setText(f"✓ {freed_str}")
            self.status.setText(f"✓ {result.message}")
            # Request immediate sample so gauges reflect freed memory
            self.sampler.set_interval(0.1)
            QtCore.QTimer.singleShot(1200, lambda: self.sampler.set_interval(self.interval_box.currentData()))
        else:
            self.clean_btn.setText("Clean RAM")
            self.mem_clean_btn.setText("⚡ Clean Standby Cache")
            self.status.setText(f"⚠️ {result.message}")

        QtCore.QTimer.singleShot(3000, self._reset_clean_buttons)

    def _reset_clean_buttons(self) -> None:
        self.clean_btn.setEnabled(True)
        self.mem_clean_btn.setEnabled(True)
        if self._last and self._last.memory:
            c = self._last.memory.standby_bytes if self._last.memory.standby_bytes is not None else self._last.memory.cached_bytes
            if c is not None and c > 0:
                cstr = human_gb(c)
                self.clean_btn.setText(f"Clean RAM (~{cstr})")
                self.mem_clean_btn.setText(f"⚡ Clean Standby (~{cstr})")
                return
        self.clean_btn.setText("Clean RAM")
        self.mem_clean_btn.setText("⚡ Clean Standby Cache")

    def closeEvent(self, event) -> None:  # noqa: N802
        if hasattr(self, "_clean_thread") and self._clean_thread.isRunning():
            self._clean_thread.wait(1000)
        self._anim.stop()
        self._clock.stop()
        self.sampler.stop()
        super().closeEvent(event)

    # ------------------------------------------------------------ snapshot

    def _on_failed(self, message: str) -> None:
        self.status.setText(f"\u26a0 {message}")

    def _on_notes(self, notes: List[str]) -> None:
        self._notes = list(notes)

    def _on_snapshot(self, snap: Snapshot) -> None:
        self._last = snap
        self._history["cpu"].append(snap.cpu.usage)
        self._history["mem"].append(snap.memory.usage)
        first_gpu = snap.gpus[0].usage if snap.gpus else None
        self._history["gpu"].append(first_gpu)

        self._render_cpu(snap)
        self._render_memory(snap)
        self._render_gpu(snap)
        self._render_cores(snap)
        self._render_status(snap)

    # -------------------------------------------------------------- render

    def _render_cpu(self, snap: Snapshot) -> None:
        cpu = snap.cpu
        self.cpu_gauge.set_value(cpu.usage)
        self.cpu_spark.set_values(list(self._history["cpu"]))

        cores = cpu.cores_logical or 0
        phys = f"{cpu.cores_physical}C / {cores}T" if cpu.cores_physical else f"{cores} threads"
        self.cpu_card.set_badge(phys, T.FG_DIM)
        self.cpu_name.setFullText(
            cpu.name if cpu.name and cpu.name != "Unknown CPU" else "\u2014")

        no_freq = ("Windows exposes no live-clock API for this CPU.\n"
                   "PDH 'Processor Frequency' reports the nominal base clock on most\n"
                   "CPUs, so sysmon labels it as such rather than pass it off as live.")
        if cpu.has_frequency:
            self.cpu_freq.set_label("Frequency")
            self.cpu_freq.set_value(human_freq(cpu.frequency_mhz), T.qcolor(T.FG),
                                    "Live core clock from the optional sensor provider")
        elif cpu.base_clock_mhz:
            self.cpu_freq.set_label("Frequency (base)")
            self.cpu_freq.set_value(
                f"{cpu.base_clock_mhz / 1000.0:.2f} GHz", T.qcolor(T.FG_MUTED),
                no_freq + f"\n\nBase clock from the registry: {cpu.base_clock_mhz:.0f} MHz")
        else:
            self.cpu_freq.set_label("Frequency")
            self.cpu_freq.set_value("N/A", T.qcolor(T.FG_DIM), no_freq)

        if cpu.frequency_max_mhz:
            self.cpu_max.set_value(human_freq(cpu.frequency_max_mhz), T.qcolor(T.FG),
                                   f"Max boost frequency for {cpu.name}")
        else:
            self.cpu_max.set_value(
                "N/A", T.qcolor(T.FG_DIM),
                "Max/boost frequency not found for this CPU model.")

        from ..cleaner import is_admin
        from ..sensors.thermal import read_ipc_temperature
        if cpu.temperature_c is not None:
            self.cpu_temp.set_value(human_temp(cpu.temperature_c), T.temp_q(cpu.temperature_c),
                                    "CPU package temperature (Tctl/Tdie)")
            if hasattr(self, "admin_btn") and self.admin_btn is not None:
                self.admin_btn.setText("🛡️ Temp Active")
                self.admin_btn.setEnabled(False)
                self.admin_btn.setToolTip("CPU temperature sensor is actively streaming")
                self.admin_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {T.alpha_css(T.qcolor(T.ONLINE, 18))};
                    border: 1px solid {T.alpha_css(T.qcolor(T.ONLINE, 65))};
                    border-radius: 6px;
                    color: {T.ONLINE};
                    padding: 2px 9px;
                    font-size: 11px;
                    font-weight: 600;
                }}
                """)
        elif not is_admin() and read_ipc_temperature() is None:
            self.cpu_temp.set_value("N/A (click to elevate)", T.qcolor(T.ACCENT),
                                    "CPU temperature requires Administrator privileges.\n"
                                    "Click here or 'Admin' in the toolbar to enable.")
        else:
            self.cpu_temp.set_value("N/A", T.qcolor(T.FG_DIM),
                                    "No CPU temperature sensor reported by hardware monitor")

        if cpu.has_power:
            self.cpu_power.set_value(human_watt(cpu.power_w), T.qcolor(T.FG),
                                    "CPU package power draw")
        else:
            self.cpu_power.set_value("N/A", T.qcolor(T.FG_DIM),
                                    "No CPU power sensor available")

    def _render_stat(self, row_widget: StatRow, value, formatter, tip: str) -> None:
        """Set a StatRow from a raw value, or an honest ``N/A`` with a reason."""
        if value is None:
            row_widget.set_value("N/A", T.qcolor(T.FG_DIM), tip)
        else:
            row_widget.set_value(formatter(value), None, "")

    def _render_memory(self, snap: Snapshot) -> None:
        mem = snap.memory
        self.mem_bar.set_value(mem.usage)
        self.mem_spark.set_values(list(self._history["mem"]))
        self.mem_card.set_badge(human_gb(mem.total_bytes, 0), T.FG_DIM)
        self.mem_used.set_value(human_gb(mem.used_bytes), T.qcolor(T.FG))
        self.mem_avail.set_value(human_gb(mem.available_bytes), T.qcolor(T.FG))
        if mem.committed_bytes and mem.commit_limit_bytes:
            # The card is narrow, so the percentage is the headline and the byte
            # counts live in the tooltip.
            self.mem_commit.set_value(f"{mem.commit_usage:.0f}%", T.qcolor(T.FG),
                                      f"Committed {human_gb(mem.committed_bytes)} of "
                                      f"{human_gb(mem.commit_limit_bytes)}")
        else:
            self.mem_commit.set_value("N/A", T.qcolor(T.FG_DIM),
                                      "GetPerformanceInfo reported no commit figures")

        # Standby / Junk cleanable cache
        cache_val = mem.standby_bytes if mem.standby_bytes is not None else mem.cached_bytes
        if cache_val is not None:
            cache_str = human_gb(cache_val)
            self.mem_cache.set_value(
                cache_str, T.qcolor(T.FG),
                f"Estimated cleanable RAM: ~{cache_str}\n"
                "Standby cache & closed application memory that can be safely purged.\n"
                "Click 'Clean Standby' to flush and free this memory."
            )
            if not getattr(self, "_cleaning_ram", False):
                self.clean_btn.setText(f"Clean RAM (~{cache_str})")
                self.clean_btn.setToolTip(f"Purge ~{cache_str} standby RAM cache left by closed apps (frees memory)")
                self.mem_clean_btn.setText(f"⚡ Clean Standby (~{cache_str})")
                self.mem_clean_btn.setToolTip(f"Purge ~{cache_str} standby RAM cache left by closed apps without affecting active programs")
        else:
            self.mem_cache.set_value("N/A", T.qcolor(T.FG_DIM), "Standby list cache size not available")

    def _render_gpu(self, snap: Snapshot) -> None:
        gpus = list(snap.gpus)
        if not gpus:
            self.gpu_gauge.set_value(None)
            self.gpu_gauge.set_na_reason(
                "NVML is unavailable or there is no NVIDIA GPU on this machine")
            self.gpu_card.set_badge("", T.FG_DIM)
            self.gpu_name.setFullText("no NVIDIA GPU detected")
            self.gpu_spark.set_values(list(self._history["gpu"]))
            for row_widget in (self.gpu_temp, self.gpu_power, self.gpu_core, self.gpu_memclk):
                row_widget.set_value("N/A", T.qcolor(T.FG_DIM), "No NVIDIA GPU present")
            self.vram_bar.set_value(None)
            return

        gpu = gpus[0]
        self.gpu_gauge.set_value(gpu.usage)
        self.gpu_spark.set_values(list(self._history["gpu"]))
        self.gpu_name.setFullText(gpu.name)
        badge = [f"GPU{g.index}" for g in gpus[1:]]
        if gpu.driver_version:
            badge.append(f"driver {gpu.driver_version}")
        self.gpu_card.set_badge("  \u00b7  ".join(badge), T.FG_DIM)

        temp = gpu.temperature_c
        if temp is None:
            self.gpu_temp.set_value("N/A", T.qcolor(T.FG_DIM),
                                    "This GPU does not expose a die temperature")
        else:
            self.gpu_temp.set_value(human_temp(temp), T.temp_q(temp))

        power = gpu.power_w
        if power is None:
            self.gpu_power.set_label("Power")
            self.gpu_power.set_value("N/A", T.qcolor(T.FG_DIM),
                                     "NVML reported no power reading")
        else:
            if gpu.power_limit_w:
                self.gpu_power.set_label(f"Power (cap {gpu.power_limit_w:.0f} W)")
            else:
                self.gpu_power.set_label("Power")
            # Colour by how much of the power budget is in use, not by raw watts.
            self.gpu_power.set_value(
                human_watt(power),
                T.severity_q(power / gpu.power_limit_w * 100.0 if gpu.power_limit_w else None))

        self.gpu_core.set_value(human_freq(gpu.clock_mhz),
                                T.qcolor(T.FG) if gpu.clock_mhz is not None else T.qcolor(T.FG_DIM))
        self.gpu_memclk.set_value(human_freq(gpu.clock_memory_mhz),
                                  T.qcolor(T.FG) if gpu.clock_memory_mhz is not None
                                  else T.qcolor(T.FG_DIM))
        if gpu.clock_max_mhz and gpu.fan_percent is not None:
            self.gpu_core.setToolTip(f"Max {human_freq(gpu.clock_max_mhz)}  \u00b7  "
                                     f"fan {gpu.fan_percent}%")
        self.vram_bar.set_value(gpu.memory_usage)

    def _render_cores(self, snap: Snapshot) -> None:
        values = [c.usage for c in snap.cpu.per_core]
        self.core_grid.set_values(values)
        if self.cores_card is not None:
            self.cores_card.set_badge(
                f"{len(values)} logical" if values else "unavailable", T.FG_DIM)

    def _render_status(self, snap: Snapshot) -> None:
        bits = []
        if self._enabled["GPU"] and snap.gpus:
            bits.append(f"GPU driver {snap.gpus[0].driver_version or '?'}")
        bits.append(f"refresh {self.sampler.interval:g}s")
        if self._paused:
            bits.append("PAUSED")
        status = "  \u00b7  ".join(bits)
        if self._notes:
            status += (f"  \u00b7  {len(self._notes)} sensor(s) unavailable "
                       "\u2014 press i for details")
        self.status.setText(status)

        # One-line summary under the window title, once real data has arrived.
        parts = [p for p in (snap.cpu.name if snap.cpu.name != "Unknown CPU" else None,
                             snap.gpus[0].name if snap.gpus else None) if p]
        self.subtitle.setText("  \u00b7  ".join(parts) if parts else "waiting for sensors\u2026")

    # --------------------------------------------------------- diagnostics

    def _show_diagnostics(self) -> None:
        from ..diagnose import report

        # report() works without a live collector, so the dialog can be opened
        # before the first sample arrives.
        lines = report(None)
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("sysmon \u2014 sensor diagnostics")
        dialog.resize(880, 620)
        dialog.setStyleSheet(T.STYLESHEET)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        view = QtWidgets.QPlainTextEdit("\n".join(lines))
        view.setReadOnly(True)
        view.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {T.INPUT};
                border: 1px solid {T.CARD_BORDER};
                border-radius: 10px;
                padding: 10px;
                font-family: 'Cascadia Mono', 'Consolas', monospace;
                font-size: 12px;
            }}
        """)
        layout.addWidget(view, 1)
        close = QtWidgets.QPushButton("Close")
        close.setStyleSheet(self._button_qss())
        close.clicked.connect(dialog.accept)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)
        dialog.exec()
