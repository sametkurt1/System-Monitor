"""Colours and glyphs, in the spirit of btop but simplified.

All styling goes through :class:`Style` so a palette swap is a single-object change.
Colours are 24-bit SGR sequences; when the terminal lacks colour support every
:meth:`Style.paint` call degrades to plain text automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


class Palette:
    """Truecolor SGR helpers."""

    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    DIM = "\x1b[2m"
    ITALIC = "\x1b[3m"
    REVERSE = "\x1b[7m"

    @staticmethod
    def fg(rgb: Tuple[int, int, int]) -> str:
        return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"

    @staticmethod
    def bg(rgb: Tuple[int, int, int]) -> str:
        return f"\x1b[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


# Foreground colours tuned to read well on both light and dark terminal backgrounds.
FG_BORDER = (78, 92, 110)
FG_BORDER_BRIGHT = (110, 128, 150)
FG_TITLE = (196, 214, 236)
FG_TEXT = (208, 218, 230)
FG_LABEL = (128, 146, 166)
FG_VALUE = (236, 242, 248)
FG_DIM = (96, 108, 124)
FG_ACCENT = (94, 178, 255)
FG_OK = (120, 220, 160)

GPU_HUES = [
    (120, 190, 255),
    (170, 160, 255),
    (255, 170, 200),
    (150, 220, 190),
]

# Severity ramp used for every percentage / temperature / wattage gauge.
SEVERITY = [
    (0.0, (86, 156, 214)),    # calm blue
    (0.45, (108, 208, 145)),  # healthy green
    (0.70, (222, 200, 96)),   # warm yellow
    (0.85, (238, 152, 76)),   # orange
    (0.95, (232, 90, 84)),    # red
]

# Temperature uses a slightly hotter ramp: it should turn yellow earlier.
TEMP_SEVERITY = [
    (0.0, (86, 156, 214)),
    (0.40, (96, 200, 200)),
    (0.58, (120, 214, 130)),
    (0.72, (226, 202, 92)),
    (0.84, (240, 146, 70)),
    (0.92, (232, 78, 78)),
]

BAR_FULL = "\u2588\u2589\u258a\u258b\u258c\u258d\u258e\u258f"
BAR_EMPTY = "\u2591"
SPARK = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def severity_color(fraction: Optional[float], ramp=None) -> Tuple[int, int, int]:
    """Map a 0..1+ fraction onto a colour from ``ramp`` (default: :data:`SEVERITY`)."""
    if fraction is None:
        return FG_DIM
    if ramp is None:
        ramp = SEVERITY
    f = max(0.0, min(1.0, fraction))
    color = ramp[0][1]
    for edge, c in ramp:
        if f >= edge:
            color = c
        else:
            break
    return color


def temp_color(celsius: Optional[float]) -> Tuple[int, int, int]:
    """Colour for an absolute temperature, or ``FG_DIM`` when unknown."""
    if celsius is None:
        return FG_DIM
    # Anchor the ramp at 40..100 C rather than 0..100 so the colours read sensibly.
    return severity_color((celsius - 40.0) / 60.0, TEMP_SEVERITY)


@dataclass(frozen=True)
class Style:
    """Applies (or suppresses) colour depending on terminal capability."""

    color: bool = True

    def paint(self, text: str, rgb: Optional[Tuple[int, int, int]] = None,
              bold: bool = False, dim: bool = False) -> str:
        if not self.color or not text:
            return text
        prefix = ""
        if bold:
            prefix += Palette.BOLD
        if dim:
            prefix += Palette.DIM
        if rgb is not None:
            prefix += Palette.fg(rgb)
        if not prefix:
            return text
        return prefix + text + Palette.RESET

    def with_bg(self, text: str, rgb: Tuple[int, int, int]) -> str:
        if not self.color:
            return text
        return Palette.bg(rgb) + text + Palette.RESET

    # Convenience wrappers used all over the layout code.
    def border(self, text: str, bright: bool = False) -> str:
        return self.paint(text, FG_BORDER_BRIGHT if bright else FG_BORDER)

    def title(self, text: str) -> str:
        return self.paint(text, FG_TITLE, bold=True)

    def label(self, text: str) -> str:
        return self.paint(text, FG_LABEL)

    def value(self, text: str) -> str:
        return self.paint(text, FG_VALUE)

    def dim(self, text: str) -> str:
        return self.paint(text, FG_DIM, dim=True)

    def accent(self, text: str) -> str:
        return self.paint(text, FG_ACCENT)

    def na(self) -> str:
        return self.paint("N/A", FG_DIM, dim=True)
