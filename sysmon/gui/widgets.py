"""Custom-painted widgets for the sysmon window.

Everything is drawn with QPainter rather than assembled from stock Qt controls, which
is what makes it look designed instead of assembled.  All gauges accept ``None`` for
their value and then render as a muted ``N/A`` - the GUI never invents a reading.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme as T

# Ease values toward their target so 1 Hz updates read as motion, not jumps.
_EASE = 0.22


class Animated:
    """Mixin giving a widget a smoothly interpolated numeric value."""

    def set_value(self, value: Optional[float]) -> None:
        raise NotImplementedError

    def tick(self) -> None:
        raise NotImplementedError


class _Eased:
    """Shared easing state: snap on the first value, ease afterwards."""

    def _init_ease(self) -> None:
        self._target: Optional[float] = None
        self._current = 0.0
        self._primed = False

    def _set(self, value: Optional[float]) -> bool:
        value = None if value is None else max(0.0, min(100.0, float(value)))
        if value == self._target:
            return False
        self._target = value
        if not self._primed and value is not None:
            # First real reading: no animation, so the UI is correct immediately.
            self._current = value
            self._primed = True
        return True

    def _ease(self) -> bool:
        if self._target is None:
            return False
        delta = self._target - self._current
        if abs(delta) < 0.05:
            self._current = self._target
            return False
        self._current += delta * _EASE
        return True


class Gauge(_Eased, QtWidgets.QWidget):
    """A 270-degree radial meter."""

    def __init__(self, accent: str = T.ACCENT, unit: str = "%",
                 caption: str = "", parent=None) -> None:
        super().__init__(parent)
        self._init_ease()
        self._unit = unit
        self._caption = caption
        self._accent = QtGui.QColor(accent)
        self._na_reason = ""
        # Fixed width keeps the meter readable; height may grow into spare card
        # space, and the dial is drawn from min(width, height) so it stays circular.
        self.setFixedWidth(178)
        self.setMinimumHeight(178)
        self.setMaximumHeight(248)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Expanding)

    def set_value(self, value: Optional[float]) -> None:
        if self._set(value):
            self.update()

    def set_na_reason(self, reason: str) -> None:
        if reason != self._na_reason:
            self._na_reason = reason
            self.update()

    def tick(self) -> None:
        if self._ease():
            self.update()

    # ------------------------------------------------------------- painting

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)

        side = max(40.0, min(self.width(), self.height()))
        width = max(6.0, side * 0.054)
        radius = side / 2.0 - width / 2.0 - 4.0
        # Centre the dial in whatever height the card gives us.
        centre = QtCore.QPointF(self.width() / 2.0, self.height() / 2.0)
        box = QtCore.QRectF(centre.x() - radius, centre.y() - radius, radius * 2, radius * 2)

        # Subtle, elegant background track
        p.setPen(T.pen(T.TRACK, width, cap=QtCore.Qt.RoundCap))
        start = 225 * 16
        span = -270 * 16
        p.drawArc(box, start, span)

        if self._target is None:
            self._paint_na(p, centre, side, width)
            return

        frac = max(0.0, min(1.0, self._current / 100.0))
        color = T.severity_q(self._current)

        if frac > 0.005:
            # Soft subtle halo underneath
            p.setPen(T.pen(T.alpha(color, 30), width * 1.7, cap=QtCore.Qt.RoundCap))
            p.drawArc(box, start, int(span * frac))
            # Crisp value arc on top
            p.setPen(T.pen(color, width, cap=QtCore.Qt.RoundCap))
            p.drawArc(box, start, int(span * frac))

            # Precision pinhead dot at arc tip
            ang = math.radians(225 - 270 * frac)
            dot = QtCore.QPointF(centre.x() + radius * math.cos(ang),
                                 centre.y() - radius * math.sin(ang))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 210)))
            p.drawEllipse(dot, width * 0.18, width * 0.18)

        self._paint_readout(p, centre, side)

    def _paint_readout(self, p: QtGui.QPainter, centre: QtCore.QPointF, side: float) -> None:
        val_str = f"{self._current:.0f}"
        num_font = T.font(side * 0.28, QtGui.QFont.Weight.DemiBold, family=T.NUM_FAMILY, tabular=True)
        unit_font = T.font(side * 0.11, QtGui.QFont.Weight.Medium, family=T.NUM_FAMILY)

        fm_num = QtGui.QFontMetricsF(num_font)
        num_w = fm_num.horizontalAdvance(val_str)
        num_h = fm_num.capHeight()

        fm_unit = QtGui.QFontMetricsF(unit_font)
        unit_w = fm_unit.horizontalAdvance(self._unit) if self._unit else 0.0
        unit_h = fm_unit.capHeight()

        total_w = num_w + (unit_w + 3.0 if self._unit else 0.0)
        start_x = centre.x() - total_w / 2.0
        base_y = centre.y() + num_h * 0.40

        # Primary value
        p.setFont(num_font)
        p.setPen(T.pen(T.FG_TITLE))
        p.drawText(QtCore.QPointF(start_x, base_y), val_str)

        # Unit symbol cleanly placed beside the number
        if self._unit:
            p.setFont(unit_font)
            p.setPen(T.pen(T.FG_MUTED))
            unit_y = base_y - (num_h - unit_h) * 0.15
            p.drawText(QtCore.QPointF(start_x + num_w + 3.0, unit_y), self._unit)

    def _paint_na(self, p: QtGui.QPainter, centre: QtCore.QPointF, side: float,
                  width: float) -> None:
        p.setPen(T.pen(T.FG_DIM))
        p.setFont(T.font(side * 0.19, QtGui.QFont.Weight.DemiBold))
        p.drawText(QtCore.QRectF(centre.x() - side, centre.y() - side * 0.22,
                                side * 2, side * 0.35),
                   QtCore.Qt.AlignCenter, "N/A")
        if self._caption:
            p.setFont(T.font(side * 0.075, QtGui.QFont.Weight.Medium))
            p.setPen(T.pen(T.alpha(self._accent, 170)))
            p.drawText(QtCore.QRectF(centre.x() - side, centre.y() + side * 0.12,
                                    side * 2, side * 0.20),
                       QtCore.Qt.AlignCenter, f"no {self._caption} sensor")


class BarGauge(_Eased, QtWidgets.QWidget):
    """Rounded horizontal bar with a label and an optional trailing value."""

    def __init__(self, label: str = "", accent: str = T.ACCENT, unit: str = "%",
                 height: int = 22, show_value: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._init_ease()
        self._label = label
        self._unit = unit
        self._accent = QtGui.QColor(accent)
        self._show_value = show_value
        self._na_reason = ""
        self.setFixedHeight(height)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def set_value(self, value: Optional[float]) -> None:
        if self._set(value):
            self.update()

    def set_na_reason(self, reason: str) -> None:
        if reason != self._na_reason:
            self._na_reason = reason
            self.update()

    def tick(self) -> None:
        if self._ease():
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        h = self.height()
        pad = 1.0
        fm = T.font(11.0, QtGui.QFont.Weight.DemiBold, family=T.NUM_FAMILY)
        value_w = 0.0
        if self._show_value:
            text = f"{self._current:.0f}{self._unit}" if self._target is not None else "N/A"
            p.setFont(fm)
            value_w = QtGui.QFontMetricsF(fm).horizontalAdvance(text) + 8

        label_w = 0.0
        if self._label:
            p.setFont(T.font(11.0, QtGui.QFont.Weight.Medium))
            label_w = QtGui.QFontMetricsF(p.font()).horizontalAdvance(self._label) + 12

        x = pad
        if self._label:
            p.setPen(T.pen(T.FG_MUTED))
            p.drawText(QtCore.QRectF(pad, 0, label_w, h),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, self._label)
            x += label_w

        bar_h = 5.0
        track = QtCore.QRectF(x, h / 2.0 - bar_h / 2.0,
                              max(10.0, self.width() - x - value_w - pad), bar_h)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QBrush(T.TRACK))
        p.drawRoundedRect(track, bar_h / 2.0, bar_h / 2.0)

        if self._target is not None and self._current > 0.4:
            frac = max(0.0, min(1.0, self._current / 100.0))
            color = T.severity_q(self._current)
            fill = QtCore.QRectF(track)
            fill.setWidth(max(bar_h, track.width() * frac))
            grad = QtGui.QLinearGradient(fill.topLeft(), fill.topRight())
            grad.setColorAt(0.0, T.alpha(color, 200))
            grad.setColorAt(1.0, color)
            p.setBrush(QtGui.QBrush(grad))
            p.drawRoundedRect(fill, bar_h / 2.0, bar_h / 2.0)
        elif self._target is None:
            p.setBrush(QtGui.QBrush(T.alpha(T.FG_DIM, 50)))
            p.drawRoundedRect(track, bar_h / 2.0, bar_h / 2.0)

        if self._show_value:
            p.setFont(fm)
            p.setPen(T.pen(T.severity_q(self._current) if self._target is not None
                                else T.FG_DIM))
            text = f"{self._current:.0f}{self._unit}" if self._target is not None else "N/A"
            p.drawText(QtCore.QRectF(self.width() - value_w, 0, value_w, h),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, text)


class Sparkline(QtWidgets.QWidget):
    """Filled area trend of a percentage series."""

    def __init__(self, accent: str = T.ACCENT, height: int = 46, ramp=None,
                 parent=None) -> None:
        super().__init__(parent)
        self._values: List[Optional[float]] = []
        self._accent = QtGui.QColor(accent)
        self._ramp = ramp
        self._base_height = height
        # Grows a little into spare space but never balloons into a solid block.
        self.setMinimumHeight(height)
        self.setMaximumHeight(height + 72)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)

    def sizeHint(self):  # noqa: N802
        return QtCore.QSize(0, self._base_height)

    def set_values(self, values: Sequence[Optional[float]]) -> None:
        self._values = list(values)[-400:]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        if w < 4 or h < 4:
            return
        box = QtCore.QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        p.setPen(T.pen(T.CARD_BORDER, 1.0))
        p.setBrush(QtGui.QBrush(T.qcolor(T.INPUT)))
        p.drawRoundedRect(box, 6.0, 6.0)

        values = [v for v in self._values if v is not None]
        hint = "collecting history\u2026"
        if len(values) < 3:
            # Too few points for a meaningful area; drawing one would look like a blob.
            p.setPen(T.pen(T.FG_DIM))
            p.setFont(T.font(10.0))
            p.drawText(box, QtCore.Qt.AlignCenter,
                       f"{hint}  ({len(values)}/{3})" if values else hint)
            return

        n = len(self._values)
        step = (box.width() - 8.0) / max(1, n - 1)
        top = box.top() + 6.0
        bottom = box.bottom() - 6.0
        span = max(1.0, bottom - top)
        peak = 100.0

        points: List[QtCore.QPointF] = []
        for i, v in enumerate(self._values):
            if v is None:
                continue
            y = bottom - (max(0.0, min(peak, v)) / peak) * span
            points.append(QtCore.QPointF(box.left() + 4.0 + i * step, y))
        if len(points) < 2:
            return
        last = self._values[-1]
        color = T.severity_q(last, self._ramp) if last is not None else self._accent

        # Smooth spline interpolation
        path = QtGui.QPainterPath()
        path.moveTo(points[0])
        if len(points) == 2:
            path.lineTo(points[1])
        else:
            for i in range(1, len(points)):
                p0 = points[i - 1]
                p1 = points[i]
                mid = QtCore.QPointF((p0.x() + p1.x()) / 2.0, (p0.y() + p1.y()) / 2.0)
                path.quadTo(p0, mid)
            path.lineTo(points[-1])

        # Soft gradient area fill
        area = QtGui.QPainterPath(path)
        area.lineTo(points[-1].x(), bottom)
        area.lineTo(points[0].x(), bottom)
        area.closeSubpath()

        grad = QtGui.QLinearGradient(QtCore.QPointF(0, top), QtCore.QPointF(0, bottom))
        grad.setColorAt(0.0, T.alpha(color, 45))
        grad.setColorAt(1.0, T.alpha(color, 0))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QBrush(grad))
        p.drawPath(area)

        # Smooth line path
        p.setBrush(QtCore.Qt.NoBrush)
        p.setPen(T.pen(color, 1.6))
        p.drawPath(path)

        # Precision point at the newest reading
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QBrush(T.alpha(color, 65)))
        p.drawEllipse(points[-1], 4.0, 4.0)
        p.setBrush(QtGui.QBrush(color))
        p.drawEllipse(points[-1], 2.0, 2.0)


class StatRow(QtWidgets.QWidget):
    """One `label ....... value` line, dimmed when the value is unavailable."""

    def __init__(self, label: str, unit: str = "", parent=None) -> None:
        super().__init__(parent)
        self._label = label
        self._unit = unit
        self._text = "N/A"
        self._color = T.qcolor(T.FG_DIM)
        self.setFixedHeight(22)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def set_value(self, text: str, color: QtGui.QColor = None, tip: str = "") -> None:
        self._text = text
        if color is not None:
            self._color = color
        self.setToolTip(tip or "")
        self.update()

    def set_label(self, label: str) -> None:
        if label != self._label:
            self._label = label
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        h = self.height()
        gap = 10.0
        value_font = T.font(11.0, QtGui.QFont.Weight.DemiBold, family=T.NUM_FAMILY)
        label_font = T.font(11.0, QtGui.QFont.Weight.Medium)
        text = f"{self._text}{self._unit}" if self._text != "N/A" else "N/A"

        p.setFont(value_font)
        p.setPen(T.pen(self._color))
        p.drawText(QtCore.QRectF(0, 0, self.width(), h),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, text)

        # Elide the label rather than let it collide with the value.
        p.setFont(label_font)
        p.setPen(T.pen(T.FG_MUTED))
        available = max(0.0, self.width()
                        - QtGui.QFontMetricsF(value_font).horizontalAdvance(text) - gap)
        p.drawText(QtCore.QRectF(0, 0, available, h),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                   QtGui.QFontMetricsF(label_font).elidedText(
                       self._label, QtCore.Qt.ElideRight, available))


class CoreGrid(QtWidgets.QWidget):
    """Compact heat grid, one cell per logical processor."""

    clicked = QtCore.Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._values: List[Optional[float]] = []
        self._hover = -1
        self.setMouseTracking(True)
        self.setMinimumHeight(64)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    def set_values(self, values: Sequence[Optional[float]]) -> None:
        self._values = list(values)
        self.update()

    def _geometry(self):
        n = len(self._values)
        if n == 0:
            return 0, 0, QtCore.QRectF(), 0.0
        cols = n
        for candidate in (8, 6, 4, 3, 2, 1):
            if n % candidate == 0 and n // candidate <= 8:
                cols = candidate
                break
        else:
            cols = min(n, 8)
        rows = math.ceil(n / cols)
        gap = 4.0
        outer = 2.0
        cw = (self.width() - 2 * outer - (cols - 1) * gap) / max(1, cols)
        ch = (self.height() - 2 * outer - (rows - 1) * gap) / max(1, rows)
        cw = max(18.0, min(cw, 132.0))
        ch = max(16.0, min(ch, 46.0))
        used_w = cols * cw + (cols - 1) * gap
        x0 = (self.width() - used_w) / 2.0
        return cols, rows, QtCore.QRectF(x0, outer, used_w, rows * ch + (rows - 1) * gap), gap

    def _cell_rects(self):
        cols, rows, block, gap = self._geometry()
        if not cols:
            return []
        rects = []
        cw = (block.width() - (cols - 1) * gap) / cols
        ch = (block.height() - (rows - 1) * gap) / rows
        for i in range(len(self._values)):
            r, c = divmod(i, cols)
            rects.append(QtCore.QRectF(block.left() + c * (cw + gap),
                                       block.top() + r * (ch + gap), cw, ch))
        return rects

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        rects = self._cell_rects()
        hit = -1
        pos = event.position()
        for i, r in enumerate(rects):
            if r.adjusted(-2, -2, 2, 2).contains(pos):
                hit = i
                break
        if hit != self._hover:
            self._hover = hit
            self.setToolTip(
                f"Logical processor {hit}\n"
                + ("N/A" if hit < 0 or self._values[hit] is None
                   else f"{self._values[hit]:.0f}% busy")
                if hit >= 0 else "")
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = -1
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        for i, r in enumerate(self._cell_rects()):
            if r.contains(event.position()):
                self.clicked.emit(i)
                return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        rects = self._cell_rects()
        if not rects:
            p.setPen(T.pen(T.FG_DIM))
            p.setFont(T.font(11))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, "per-core data unavailable")
            return
        for i, r in enumerate(rects):
            v = self._values[i] if i < len(self._values) else None

            # 1. Base tile
            p.setPen(T.pen(T.CARD_BORDER, 1.0))
            p.setBrush(QtGui.QBrush(T.qcolor(T.INPUT)))
            p.drawRoundedRect(r, 5.0, 5.0)

            # 2. Activity representation
            if v is not None:
                frac = max(0.0, min(1.0, v / 100.0))
                color = T.severity_q(v)

                # Translucent glow tint (sleek, not solid opaque)
                tint = T.alpha(color, 20 + int(85 * frac))
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(QtGui.QBrush(tint))
                p.drawRoundedRect(r, 5.0, 5.0)

                # Sleek bottom mini-bar
                bar_h = max(2.5, min(4.0, r.height() * 0.12))
                bar_pad = 4.0
                bar_max_w = max(4.0, r.width() - 2 * bar_pad)
                track_rect = QtCore.QRectF(r.left() + bar_pad, r.bottom() - bar_h - 3.5,
                                          bar_max_w, bar_h)

                # Track
                p.setBrush(QtGui.QBrush(T.alpha(T.TRACK, 160)))
                p.drawRoundedRect(track_rect, bar_h / 2.0, bar_h / 2.0)

                # Fill
                if frac > 0.02:
                    fill_rect = QtCore.QRectF(track_rect.left(), track_rect.top(),
                                              max(bar_h, bar_max_w * frac), bar_h)
                    p.setBrush(QtGui.QBrush(color))
                    p.drawRoundedRect(fill_rect, bar_h / 2.0, bar_h / 2.0)

            # 3. Hover border
            if i == self._hover:
                p.setBrush(QtCore.Qt.NoBrush)
                p.setPen(T.pen(T.ACCENT, 1.2))
                p.drawRoundedRect(r.adjusted(0.6, 0.6, -0.6, -0.6), 5.0, 5.0)

            # 4. Core index label
            text_rect = QtCore.QRectF(r.left(), r.top() + (1.0 if v is not None else 0.0),
                                      r.width(), r.height() - (7.0 if v is not None else 0.0))
            p.setFont(T.font(9.0, QtGui.QFont.Weight.DemiBold, family=T.NUM_FAMILY))
            p.setPen(T.pen(T.FG_TITLE if (v is not None and v > 50) else (T.FG if v is not None else T.FG_DIM)))
            p.drawText(text_rect, QtCore.Qt.AlignCenter, f"{i + 1}")


class ElidedLabel(QtWidgets.QLabel):
    """Single-line label that shortens with an ellipsis and keeps the full text
    in its tooltip, so a long CPU model never wraps or truncates mid-card."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._full = text
        self.setMinimumHeight(16)
        super().setText(text)

    def setFullText(self, text: str) -> None:  # noqa: N802 - Qt naming
        if text != self._full:
            self._full = text
            self._apply()
        # Always keep the untruncated text reachable, even if it is already current.
        self.setToolTip(text)

    def fullText(self) -> str:  # noqa: N802
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply()

    def _apply(self) -> None:
        metrics = QtGui.QFontMetricsF(self.font())
        super().setText(metrics.elidedText(self._full, QtCore.Qt.ElideRight,
                                           max(10.0, self.width() - 2.0)))


class Card(QtWidgets.QFrame):
    """Rounded surface with a title strip."""

    def __init__(self, title: str = "", accent: str = T.ACCENT,
                 parent=None) -> None:
        super().__init__(parent)
        self._accent = QtGui.QColor(accent)
        self.setObjectName("card")
        self.setStyleSheet(f"""
        QFrame#card {{
            background: {T.CARD};
            border: 1px solid {T.CARD_BORDER};
            border-radius: 12px;
        }}
        """)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 15)
        outer.setSpacing(10)
        outer.setAlignment(QtCore.Qt.AlignTop)
        self._header = QtWidgets.QHBoxLayout()
        self._header.setSpacing(8)
        self.title = QtWidgets.QLabel(title.upper())
        self.title.setFont(T.font(10.0, QtGui.QFont.Weight.Bold))
        self.title.setStyleSheet(f"color: {T.alpha_css(self._accent)}; letter-spacing: 1.2px;")
        self.badge = QtWidgets.QLabel("")
        self.badge.setFont(T.font(9.5, QtGui.QFont.Weight.Medium, family=T.NUM_FAMILY))
        self.badge.setStyleSheet(f"""
            color: {T.FG_MUTED};
            background: {T.INPUT};
            border: 1px solid {T.CARD_BORDER};
            border-radius: 4px;
            padding: 1px 6px;
        """)
        # Labels must not absorb vertical slack, or the header swallows the card.
        for label in (self.title, self.badge):
            label.setFixedHeight(18)
            label.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        self._header.addWidget(self.title)
        self._header.addStretch(1)
        self._header.addWidget(self.badge)
        outer.addLayout(self._header)
        self.body = QtWidgets.QVBoxLayout()
        self.body.setSpacing(8)
        self.body.setContentsMargins(0, 0, 0, 0)
        # Pack content to the top so a short card does not push its own title down.
        self.body.setAlignment(QtCore.Qt.AlignTop)
        outer.addLayout(self.body, 1)

    def set_badge(self, text: str, color: str = T.FG_DIM) -> None:
        self.badge.setText(text)
        self.badge.setVisible(bool(text))
        if text:
            self.badge.setStyleSheet(f"""
                color: {color};
                background: {T.INPUT};
                border: 1px solid {T.CARD_BORDER};
                border-radius: 4px;
                padding: 1px 6px;
            """)


def make_icon(size: int = 64) -> QtGui.QIcon:
    """Programmatically drawn window icon: three stacked meter arcs."""
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    inset = size * 0.18
    box = QtCore.QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    w = max(2.5, size * 0.12)
    for frac, color in ((0.92, T.GPU), (0.64, T.MEM), (0.36, T.ACCENT)):
        p.setPen(T.pen(color, w, cap=QtCore.Qt.RoundCap))
        p.drawArc(box.adjusted(w / 2, w / 2, -w / 2, -w / 2),
                  225 * 16, int(-270 * 16 * frac))
    p.end()
    return QtGui.QIcon(pm)


