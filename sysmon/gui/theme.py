"""Visual language for the sysmon desktop GUI.

Colours deliberately reuse the severity ramps from :mod:`sysmon.theme` so the window
and the terminal UI read as the same product.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui

from ..theme import SEVERITY, TEMP_SEVERITY, severity_color, temp_color

# ---------------------------------------------------------------- surfaces
BG = "#0a0c10"
BG_GRADIENT_TOP = "#0d1017"
CARD = "#12151e"
CARD_TOP = "#161b26"
CARD_BORDER = "#1e2433"
CARD_BORDER_HI = "#2d374d"
TRACK = "#181d2a"
INPUT = "#0f121a"
CARD_PILL = "#181d28"

# ----------------------------------------------------------------- text
FG = "#e2e8f0"
FG_MUTED = "#8e9eb3"
FG_DIM = "#526075"
FG_TITLE = "#f8fafc"

# ---------------------------------------------------------------- accents
ACCENT = "#38bdf8"        # CPU
GPU = "#a78bfa"          # GPU
MEM = "#34d399"          # memory
VRAM = "#f472b6"         # video memory
OK = "#34d399"
ONLINE = "#34d399"
WARN = "#fbbf24"
DANGER = "#f87171"

# Re-exported so widgets can colour by severity.
SEVERITY_RAMP = SEVERITY
TEMP_RAMP = TEMP_SEVERITY
severity = severity_color
temp_severity = temp_color


def qcolor(spec, alpha: int = 255) -> QtGui.QColor:
    """Build a QColor from an ``(r, g, b)`` tuple, a ``#rrggbb`` string or a QColor."""
    if isinstance(spec, QtGui.QColor):
        out = QtGui.QColor(spec)
    elif isinstance(spec, (tuple, list)):
        out = QtGui.QColor(int(spec[0]), int(spec[1]), int(spec[2]))
    else:
        out = QtGui.QColor(str(spec))
        if not out.isValid():
            out = QtGui.QColor(T_FALLBACK)
    if alpha != 255:
        out.setAlpha(alpha)
    return out


T_FALLBACK = (140, 150, 160)


def severity_q(value, ramp=None) -> QtGui.QColor:
    """Severity colour for a percentage, or the dim 'unknown' colour for ``None``.

    ``None`` deliberately does *not* go through the shared ramp: that ramp's fallback
    is the terminal palette's grey, which is a slightly different shade from this
    window's ``FG_DIM``.  Using it here would make an ``N/A`` gauge and an ``N/A``
    label render in two different greys.
    """
    if value is None:
        return qcolor(FG_DIM)
    return qcolor(severity_color(value / 100.0, ramp))


def temp_q(celsius) -> QtGui.QColor:
    return qcolor(temp_color(celsius))


def alpha(color: QtGui.QColor, a: int) -> QtGui.QColor:
    out = QtGui.QColor(color)
    out.setAlpha(a)
    return out


def alpha_css(color: QtGui.QColor) -> str:
    """#rrggbbaa string, for embedding a colour in a Qt stylesheet."""
    return color.name(QtGui.QColor.HexArgb)


def qcss(rgb, alpha_value: int = 255) -> str:
    return qcolor(rgb, alpha_value).name(QtGui.QColor.HexArgb)


def pen(color, width: float = 1.0,
        cap=QtCore.Qt.RoundCap, style=QtCore.Qt.SolidLine) -> QtGui.QPen:
    """A rounded pen.  PySide6 only exposes the 5-argument QPen overload, so every
    call site goes through here instead of spelling out the defaults."""
    return QtGui.QPen(qcolor(color), width, style, cap, QtCore.Qt.RoundJoin)


def best_family(candidates) -> str:
    available = set(QtGui.QFontDatabase.families())
    for name in candidates:
        if name in available:
            return name
    return candidates[-1]


UI_FAMILY = "Segoe UI"
NUM_FAMILY = "Segoe UI"


def refresh_fonts() -> None:
    """Pick the nicest available UI font once a QGuiApplication exists."""
    global UI_FAMILY, NUM_FAMILY
    UI_FAMILY = best_family([
        "Segoe UI Variable Display", "Segoe UI Variable Text", "Segoe UI",
        "Inter", "Noto Sans", "DejaVu Sans", "Arial",
    ])
    NUM_FAMILY = best_family([
        "Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans",
    ])


def font(size: float, weight: int = QtGui.QFont.Weight.Normal,
         family: str = None, tabular: bool = False) -> QtGui.QFont:
    f = QtGui.QFont(family or UI_FAMILY)
    f.setPointSizeF(size)
    f.setWeight(weight)
    if tabular:
        _make_tabular(f)
    return f


def _make_tabular(f: QtGui.QFont) -> None:
    """Ask for tabular figures so digits do not jitter as values change."""
    try:
        # Qt 6.7+: request the OpenType 'tnum' feature.
        f.setFeature("tnum", 1)
    except Exception:
        pass


STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {FG};
}}
QLabel {{
    background: transparent;
}}
QToolTip {{
    background: {CARD};
    color: {FG_TITLE};
    border: 1px solid {CARD_BORDER_HI};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 11px;
}}
QComboBox {{
    background: {INPUT};
    border: 1px solid {CARD_BORDER};
    border-radius: 6px;
    padding: 4px 10px;
    color: {FG_MUTED};
    font-size: 11.5px;
    min-width: 52px;
}}
QComboBox:hover {{
    border-color: {CARD_BORDER_HI};
    color: {FG};
}}
QComboBox::drop-down {{
    border: none;
    width: 16px;
}}
QComboBox QAbstractItemView {{
    background: {INPUT};
    border: 1px solid {CARD_BORDER_HI};
    border-radius: 6px;
    selection-background-color: {alpha_css(qcolor(ACCENT, 40))};
    selection-color: {FG_TITLE};
    color: {FG};
    padding: 4px;
    outline: none;
}}
QCheckBox {{
    color: {FG_MUTED};
    font-size: 11.5px;
    spacing: 7px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 4px;
    border: 1px solid {CARD_BORDER};
    background: {INPUT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {CARD_BORDER};
    border-radius: 3px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {CARD_BORDER_HI};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 6px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {CARD_BORDER};
    border-radius: 3px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {CARD_BORDER_HI};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}
"""
