"""In-game FPS and hardware monitoring overlay (OSD).

Renders a sleek, transparent, always-on-top, click-through HUD overlay
over PC games (DirectX 9/10/11/12, Vulkan, OpenGL), displaying live FPS,
frametimes, 1% low FPS, active game name, and GPU/CPU/RAM hardware statistics.
"""

from __future__ import annotations

import ctypes
import sys
from collections import deque
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..formatting import human_freq, human_gb, human_temp
from ..models import Snapshot
from . import theme as T

# Win32 Constants
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040


class OverlayWindow(QtWidgets.QWidget):
    """Semi-transparent in-game HUD overlay that floats over games."""

    POSITION_TOP_LEFT = "top_left"
    POSITION_TOP_RIGHT = "top_right"
    POSITION_BOTTOM_LEFT = "bottom_left"
    POSITION_BOTTOM_RIGHT = "bottom_right"

    MODE_FULL = "full"
    MODE_COMPACT = "compact"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._position = self.POSITION_TOP_LEFT
        self._mode = self.MODE_FULL
        self._click_through = True
        self._frametime_history = deque(maxlen=40)

        # Setup frameless always-on-top window
        flags = (
            QtCore.Qt.WindowType.FramelessWindowHint |
            QtCore.Qt.WindowType.WindowStaysOnTopHint |
            QtCore.Qt.WindowType.Tool |
            QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._last_snapshot = Snapshot()
        self._build_ui()
        self.apply_position()

        # Periodically enforce HWND_TOPMOST so fullscreen exclusive & borderless games cannot obscure the HUD
        self._topmost_timer = QtCore.QTimer(self)
        self._topmost_timer.setInterval(400)
        self._topmost_timer.timeout.connect(self._reinforce_topmost)
        self._topmost_timer.start()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Outer card container
        self.card = QtWidgets.QFrame()
        self.card.setObjectName("OverlayCard")
        self.card.setStyleSheet(f"""
            QFrame#OverlayCard {{
                background-color: rgba(13, 17, 23, 225);
                border: 1px solid rgba(56, 189, 248, 80);
                border-radius: 8px;
            }}
        """)
        card_layout = QtWidgets.QVBoxLayout(self.card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(5)

        # 1. Game header row
        head_row = QtWidgets.QHBoxLayout()
        head_row.setSpacing(6)
        self.icon_lbl = QtWidgets.QLabel("🎮")
        self.icon_lbl.setFont(T.font(11.0))
        self.icon_lbl.setStyleSheet("color: #38bdf8;")
        head_row.addWidget(self.icon_lbl)

        self.game_lbl = QtWidgets.QLabel("sysmon OSD")
        self.game_lbl.setFont(T.font(10.0, QtGui.QFont.Weight.Bold))
        self.game_lbl.setStyleSheet("color: #f8fafc;")
        head_row.addWidget(self.game_lbl, 1)

        self.status_dot = QtWidgets.QLabel("●")
        self.status_dot.setFont(T.font(8.0))
        self.status_dot.setStyleSheet("color: #34d399;")
        head_row.addWidget(self.status_dot)
        card_layout.addLayout(head_row)

        # 2. Main FPS Row
        fps_row = QtWidgets.QHBoxLayout()
        fps_row.setSpacing(8)
        self.fps_val = QtWidgets.QLabel("--")
        self.fps_val.setFont(T.font(24.0, QtGui.QFont.Weight.Bold, family=T.NUM_FAMILY, tabular=True))
        self.fps_val.setStyleSheet("color: #22c55e;")
        fps_row.addWidget(self.fps_val)

        fps_sub = QtWidgets.QVBoxLayout()
        fps_sub.setSpacing(1)
        self.fps_unit = QtWidgets.QLabel("FPS")
        self.fps_unit.setFont(T.font(9.0, QtGui.QFont.Weight.Bold))
        self.fps_unit.setStyleSheet("color: #94a3b8; letter-spacing: 1px;")
        self.frametime_val = QtWidgets.QLabel("-- ms")
        self.frametime_val.setFont(T.font(9.5, family=T.NUM_FAMILY, tabular=True))
        self.frametime_val.setStyleSheet("color: #cbd5e1;")
        fps_sub.addWidget(self.fps_unit)
        fps_sub.addWidget(self.frametime_val)
        fps_row.addLayout(fps_sub)
        fps_row.addStretch(1)

        self.low1_val = QtWidgets.QLabel("1% Low: --")
        self.low1_val.setFont(T.font(9.5, QtGui.QFont.Weight.DemiBold, family=T.NUM_FAMILY, tabular=True))
        self.low1_val.setStyleSheet("color: #e2e8f0; background: rgba(30, 41, 59, 160); padding: 2px 6px; border-radius: 4px;")
        fps_row.addWidget(self.low1_val)
        card_layout.addLayout(fps_row)

        # 3. Hardware Stats Section (Full mode)
        self.stats_widget = QtWidgets.QWidget()
        stats_layout = QtWidgets.QVBoxLayout(self.stats_widget)
        stats_layout.setContentsMargins(0, 4, 0, 0)
        stats_layout.setSpacing(3)

        # Separator line
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setStyleSheet("background-color: rgba(255, 255, 255, 25); height: 1px;")
        stats_layout.addWidget(sep)

        # GPU Stat Row
        self.gpu_row = QtWidgets.QHBoxLayout()
        self.gpu_row.setSpacing(6)
        gpu_tag = QtWidgets.QLabel("GPU")
        gpu_tag.setFont(T.font(9.0, QtGui.QFont.Weight.Bold))
        gpu_tag.setStyleSheet(f"color: {T.GPU}; width: 28px;")
        gpu_tag.setFixedWidth(28)
        self.gpu_txt = QtWidgets.QLabel("--% · --°C · -- GB")
        self.gpu_txt.setFont(T.font(9.5, family=T.NUM_FAMILY, tabular=True))
        self.gpu_txt.setStyleSheet("color: #f1f5f9;")
        self.gpu_row.addWidget(gpu_tag)
        self.gpu_row.addWidget(self.gpu_txt, 1)
        stats_layout.addLayout(self.gpu_row)

        # CPU Stat Row
        self.cpu_row = QtWidgets.QHBoxLayout()
        self.cpu_row.setSpacing(6)
        cpu_tag = QtWidgets.QLabel("CPU")
        cpu_tag.setFont(T.font(9.0, QtGui.QFont.Weight.Bold))
        cpu_tag.setStyleSheet(f"color: {T.ACCENT}; width: 28px;")
        cpu_tag.setFixedWidth(28)
        self.cpu_txt = QtWidgets.QLabel("--% · --°C · -- GHz")
        self.cpu_txt.setFont(T.font(9.5, family=T.NUM_FAMILY, tabular=True))
        self.cpu_txt.setStyleSheet("color: #f1f5f9;")
        self.cpu_row.addWidget(cpu_tag)
        self.cpu_row.addWidget(self.cpu_txt, 1)
        stats_layout.addLayout(self.cpu_row)

        # RAM Stat Row
        self.ram_row = QtWidgets.QHBoxLayout()
        self.ram_row.setSpacing(6)
        ram_tag = QtWidgets.QLabel("RAM")
        ram_tag.setFont(T.font(9.0, QtGui.QFont.Weight.Bold))
        ram_tag.setStyleSheet(f"color: {T.MEM}; width: 28px;")
        ram_tag.setFixedWidth(28)
        self.ram_txt = QtWidgets.QLabel("-- GB / -- GB")
        self.ram_txt.setFont(T.font(9.5, family=T.NUM_FAMILY, tabular=True))
        self.ram_txt.setStyleSheet("color: #f1f5f9;")
        self.ram_row.addWidget(ram_tag)
        self.ram_row.addWidget(self.ram_txt, 1)
        stats_layout.addLayout(self.ram_row)

        card_layout.addWidget(self.stats_widget)
        root.addWidget(self.card)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.apply_click_through()
        self.apply_position()

    def set_click_through(self, enabled: bool) -> None:
        self._click_through = enabled
        self.apply_click_through()

    def _reinforce_topmost(self) -> None:
        """Keep overlay permanently pinned above fullscreen/borderless games and DWM."""
        if sys.platform != "win32" or not self.isVisible():
            return
        try:
            hwnd = int(self.winId())
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
            )
        except Exception:
            pass

    def apply_click_through(self) -> None:
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            base_style = ex_style | WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            if self._click_through:
                new_style = base_style | WS_EX_TRANSPARENT
            else:
                new_style = base_style & ~WS_EX_TRANSPARENT
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
            user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED
            )
        except Exception:
            pass

    def set_position(self, pos: str) -> None:
        self._position = pos
        self.apply_position()

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        if mode == self.MODE_COMPACT:
            self.stats_widget.setVisible(False)
            self.card.setStyleSheet(f"""
                QFrame#OverlayCard {{
                    background-color: rgba(13, 17, 23, 215);
                    border: 1px solid rgba(56, 189, 248, 60);
                    border-radius: 8px;
                }}
            """)
        else:
            self.stats_widget.setVisible(True)
            self.card.setStyleSheet(f"""
                QFrame#OverlayCard {{
                    background-color: rgba(13, 17, 23, 225);
                    border: 1px solid rgba(56, 189, 248, 80);
                    border-radius: 8px;
                }}
            """)
        self.adjustSize()
        self.apply_position()

    def apply_position(self) -> None:
        screen = QtGui.QGuiApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        w = 260
        h = self.sizeHint().height() if self.sizeHint().height() > 0 else 160
        margin_x = 24
        margin_y = 28

        if self._position == self.POSITION_TOP_LEFT:
            x = geo.x() + margin_x
            y = geo.y() + margin_y
        elif self._position == self.POSITION_TOP_RIGHT:
            x = geo.x() + geo.width() - w - margin_x
            y = geo.y() + margin_y
        elif self._position == self.POSITION_BOTTOM_LEFT:
            x = geo.x() + margin_x
            y = geo.y() + geo.height() - h - margin_y
        elif self._position == self.POSITION_BOTTOM_RIGHT:
            x = geo.x() + geo.width() - w - margin_x
            y = geo.y() + geo.height() - h - margin_y
        else:
            x = geo.x() + margin_x
            y = geo.y() + margin_y

        self.setGeometry(x, y, w, h)

    def update_snapshot(self, snap: Snapshot) -> None:
        """Update live overlay metrics from the latest system snapshot."""
        self._last_snapshot = snap
        self._reinforce_topmost()
        fps_info = snap.fps

        # 1. Update Game Name / Status
        if fps_info and fps_info.app_name:
            clean_name = fps_info.app_name
            if clean_name.lower().endswith(".exe"):
                clean_name = clean_name[:-4]
            self.game_lbl.setText(clean_name[:24])
            if getattr(fps_info, "is_active", False):
                self.icon_lbl.setText("🎮")
                self.status_dot.setStyleSheet("color: #34d399;")  # Active game green
            else:
                self.icon_lbl.setText("🖥️")
                self.status_dot.setStyleSheet("color: #38bdf8;")  # Active desktop/compositor cyan
        else:
            self.game_lbl.setText("sysmon OSD")
            self.icon_lbl.setText("🎮")
            self.status_dot.setStyleSheet("color: #64748b;")  # Standby grey

        # 2. Update FPS & Frametime
        if fps_info and fps_info.fps is not None and fps_info.fps > 0:
            val = fps_info.fps
            self.fps_val.setText(f"{val:.0f}" if val >= 10 else f"{val:.1f}")
            # Color code FPS
            if val >= 60:
                color = "#22c55e"  # Emerald green
            elif val >= 30:
                color = "#facc15"  # Amber yellow
            else:
                color = "#f87171"  # Danger red
            self.fps_val.setStyleSheet(f"color: {color};")

            ft = fps_info.frametime_ms
            self.frametime_val.setText(f"{ft:.1f} ms" if ft else "-- ms")

            low1 = fps_info.fps_1percent_low
            self.low1_val.setText(f"1% Low: {low1:.0f}" if low1 else "1% Low: --")
        elif fps_info and not fps_info.is_available and "Administrator" in fps_info.detail:
            self.fps_val.setText("--")
            self.fps_val.setStyleSheet("color: #38bdf8;")
            self.frametime_val.setText("needs Admin")
            self.low1_val.setText("🛡️ Elevate sysmon")
        else:
            self.fps_val.setText("--")
            self.fps_val.setStyleSheet("color: #64748b;")
            self.frametime_val.setText(fps_info.detail if (fps_info and fps_info.detail) else "waiting for a game")
            self.low1_val.setText("1% Low: --")

        # 3. Update GPU Stats
        if snap.gpus:
            g = snap.gpus[0]
            g_util = f"{g.usage:.0f}%" if g.usage is not None else "--%"
            g_temp = human_temp(g.temperature_c) if g.temperature_c is not None else "--°C"
            g_vram = human_gb(g.memory_used_bytes, 1) if g.memory_used_bytes else "-- GB"
            self.gpu_txt.setText(f"{g_util} · {g_temp} · {g_vram}")
        else:
            self.gpu_txt.setText("No NVIDIA GPU")

        # 4. Update CPU Stats
        c = snap.cpu
        c_util = f"{c.usage:.0f}%" if c.usage is not None else "--%"
        c_temp = human_temp(c.temperature_c) if c.temperature_c is not None else "--°C"
        c_freq = human_freq(c.frequency_mhz) if c.has_frequency else (f"{c.base_clock_mhz / 1000.0:.1f} GHz" if c.base_clock_mhz else "-- GHz")
        self.cpu_txt.setText(f"{c_util} · {c_temp} · {c_freq}")

        # 5. Update RAM Stats
        m = snap.memory
        m_used = human_gb(m.used_bytes, 1) if m.used_bytes else "-- GB"
        m_total = human_gb(m.total_bytes, 0) if m.total_bytes else "-- GB"
        self.ram_txt.setText(f"{m_used} / {m_total} ({m.usage:.0f}%)" if m.usage is not None else f"{m_used} / {m_total}")

    def closeEvent(self, event) -> None:  # noqa: N802
        if hasattr(self, "_topmost_timer") and self._topmost_timer:
            self._topmost_timer.stop()
        super().closeEvent(event)
