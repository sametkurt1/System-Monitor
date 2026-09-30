"""Reusable drawing primitives: gauges, sparklines, panels and two-column layout."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .formatting import pad, text_width
from .theme import (
    BAR_EMPTY,
    BAR_FULL,
    SPARK,
    FG_ACCENT,
    FG_BORDER,
    FG_BORDER_BRIGHT,
    FG_DIM,
    FG_LABEL,
    FG_VALUE,
    Style,
    severity_color,
)


class Glyphs:
    """Box-drawing characters, swapped for ASCII when Unicode is unavailable."""

    def __init__(self, unicode_ok: bool = True) -> None:
        self.unicode_ok = bool(unicode_ok)
        if self.unicode_ok:
            self.tl, self.tr, self.bl, self.br = "\u256d", "\u256e", "\u2570", "\u256f"
            self.h, self.v = "\u2500", "\u2502"
            self.lt, self.rt, self.tt, self.bt, self.x = (
                "\u251c", "\u2524", "\u252c", "\u2534", "\u253c")
            self.dot = "\u00b7"
            self.arrow = "\u2192"
        else:
            self.tl = self.tr = self.bl = self.br = "+"
            self.h = self.v = "-"
            self.lt = self.rt = self.tt = self.bt = self.x = "+"
            self.dot = "."
            self.arrow = "->"
        self.bar_full = BAR_FULL if unicode_ok else "#"
        self.bar_empty = BAR_EMPTY if unicode_ok else "."
        self.spark = SPARK if unicode_ok else ".:-=+*#"


def gauge(value: Optional[float], width: int, style: Style, glyphs: Glyphs,
          ramp=None, decimals: int = 0) -> str:
    """A filled bar with optional sub-cell resolution (eighth blocks)."""
    if width <= 0:
        return ""
    if value is None:
        return style.paint(glyphs.bar_empty * width, FG_DIM)
    fraction = max(0.0, min(1.0, value / 100.0))
    color = severity_color(fraction, ramp) if ramp is not None else severity_color(fraction)
    exact = fraction * width
    full = int(exact)
    remainder = exact - full
    if full >= width:
        return style.paint(glyphs.bar_full[0] * width, color)

    body = glyphs.bar_full[0] * full
    if full < width:
        eighths = int(remainder * 8)
        if eighths > 0:
            # BAR_FULL has 8 glyphs (indices 0-7); the eighth block is the full cell.
            body += glyphs.bar_full[min(eighths, len(glyphs.bar_full) - 1)]
    filled = width - text_width(_plain(body))
    tail = glyphs.bar_empty * max(0, filled)
    return style.paint(body, color) + style.paint(tail, FG_DIM)


def _plain(s: str) -> str:
    from .formatting import strip_ansi
    return strip_ansi(s)


def bar_with_label(value: Optional[float], width: int, style: Style, glyphs: Glyphs,
                   text: str, ramp=None) -> str:
    """``[####------]  42%`` with the label right-aligned outside the bar."""
    return f"{gauge(value, width, style, glyphs, ramp)} {style.paint(text, severity_color((value or 0) / 100.0, ramp) if value is not None else FG_DIM)}"


def sparkline(values: Sequence[Optional[float]], width: int, style: Style,
              glyphs: Glyphs, ramp=None) -> str:
    """Tiny inline trend line from a series of percentages."""
    vals = [v for v in values if v is not None]
    if not vals or width <= 0:
        return style.paint(glyphs.spark[0] * max(width, 0), FG_DIM)
    if len(vals) > width:
        vals = vals[-width:]
    peak = max(100.0, max(vals))
    out = []
    for v in vals:
        idx = int(round((v / peak) * (len(glyphs.spark) - 1)))
        idx = max(0, min(len(glyphs.spark) - 1, idx))
        out.append(glyphs.spark[idx])
    pad_len = width - len(out)
    if pad_len > 0:
        out = [glyphs.spark[0]] * pad_len + out
    return style.paint("".join(out), severity_color(vals[-1] / 100.0, ramp) if vals[-1] is not None else FG_DIM)


class Gauge:
    """Namespace for the bar / trend-line primitives."""

    @staticmethod
    def bar(value, width, style, glyphs, ramp=None):
        return gauge(value, width, style, glyphs, ramp)

    @staticmethod
    def spark(values, width, style, glyphs, ramp=None):
        return sparkline(values, width, style, glyphs, ramp)

    @staticmethod
    def labeled(value, width, style, glyphs, text, ramp=None):
        return bar_with_label(value, width, style, glyphs, text, ramp)


class Panel:
    """A rounded box that collects already-rendered lines of a known inner width."""

    def __init__(self, title: str, width: int, style: Style, glyphs: Glyphs,
                 accent: Optional[Tuple[int, int, int]] = None) -> None:
        self.title = title
        self.width = max(12, width)
        self.style = style
        self.glyphs = glyphs
        self.accent = accent or FG_ACCENT
        self.rows: List[str] = []
        self.footer: List[str] = []

    @property
    def inner(self) -> int:
        """Usable cells between the two vertical borders."""
        return self.width - 4

    def add(self, line: str = "") -> None:
        self.rows.append(line)

    def render(self) -> List[str]:
        g, s = self.glyphs, self.style
        inner = self.inner
        head = s.paint(g.tl + g.h, FG_BORDER)
        if self.title:
            label = f" {self.title} "
            head += s.paint(label, self.accent, bold=True) + s.paint(g.h * max(0, inner + 2 - 1 - text_width(label)), FG_BORDER)
        else:
            head += s.paint(g.h * (inner + 1), FG_BORDER)
        head += s.paint(g.tr, FG_BORDER)
        lines = [head]
        v = s.paint(g.v, FG_BORDER)
        for row in self.rows:
            body = pad(row, inner)
            lines.append(v + " " + body + " " + v)
        for row in self.footer:
            body = pad(row, inner)
            lines.append(v + " " + body + " " + v)
        lines.append(s.paint(g.bl + g.h * (inner + 2) + g.br, FG_BORDER))
        return lines


def join_vertical(panels: Sequence[Panel], style: Style, glyphs: Glyphs,
                  gap: int = 1) -> List[str]:
    """Stack panels, inserting horizontal rules between them."""
    out: List[str] = []
    for idx, p in enumerate(panels):
        if idx:
            out.extend(_rule(p.width, style, glyphs))
            out.extend([""] * (gap - 1))
        out.extend(p.render())
    return out


def _rule(width: int, style: Style, glyphs: Glyphs) -> List[str]:
    return [style.paint(glyphs.lt + glyphs.h * (width - 2) + glyphs.rt, FG_BORDER)]


def frame(lines: Sequence[str], width: int, style: Style, glyphs: Glyphs,
          title: str = "", subtitle: str = "") -> List[str]:
    """Wrap pre-rendered lines in an outer box (used for the whole dashboard).

    The top border is always exactly ``width`` cells wide.  When the title and
    subtitle cannot both fit, the subtitle is clipped first (it carries live data)
    and only then is the title dropped.
    """
    g, s = glyphs, style
    inner = max(4, width - 2)
    # The corner glyphs occupy the same columns as the vertical borders, so the
    # title/subtitle run must be exactly `inner` cells to keep every row `width` wide.
    span = inner

    left = f" {title} " if title else ""
    right = f" {subtitle} " if subtitle else ""

    if text_width(right) > span:
        right = " " + pad(right.strip(), max(0, span - 2)) + " "
    if text_width(left) + text_width(right) > span:
        left = ""
    gap = max(0, span - text_width(left) - text_width(right))

    if right and not left:
        top = g.tl + " " * gap + s.paint(right, FG_DIM) + g.tr
    else:
        top = (g.tl + s.paint(left, FG_BORDER_BRIGHT, bold=True) + " " * gap
               + s.paint(right, FG_DIM) + g.tr)

    out: List[str] = [top]
    v = s.paint(g.v, FG_BORDER)
    for line in lines:
        out.append(v + pad(line, inner) + v)
    out.append(s.paint(g.bl + g.h * inner + g.br, FG_BORDER))
    return out


def columns(left: Sequence[str], right: Sequence[str], width: int, style: Style,
            glyphs: Glyphs, gap: int = 3) -> List[str]:
    """Place two side-by-side blocks of lines into a single block."""
    half = (width - gap) // 2
    right_width = width - half - gap
    height = max(len(left), len(right))
    out: List[str] = []
    for i in range(height):
        l = pad(left[i], half) if i < len(left) else " " * half
        r = pad(right[i], right_width) if i < len(right) else ""
        out.append(l + " " * gap + r)
    return out


def kv(label: str, value: str, style: Style, label_width: int = 13,
       value_color=None, label_color=FG_LABEL) -> str:
    """``Label   value`` with the value optionally coloured."""
    ltext = pad(style.paint(label, label_color), label_width)
    vtext = style.paint(value, value_color) if value_color is not None else style.paint(value, FG_VALUE)
    return ltext + vtext


def core_bar(index: int, usage: Optional[float], width: int, style: Style,
             glyphs: Glyphs) -> str:
    """One entry of the per-core grid: ``C01 #######--  71%``."""
    name = style.paint(f"C{index + 1:02d}", FG_LABEL)
    g = gauge(usage, width, style, glyphs)
    if usage is None:
        text = style.paint("N/A", FG_DIM, dim=True)
    else:
        text = style.paint(f"{usage:3.0f}%", severity_color(usage / 100.0))
    return f"{name} {g} {text}"


def grid(cells: Sequence[str], columns_: int, width: int, style: Style,
         glyphs: Glyphs, gap: int = 2) -> List[str]:
    """Lay a flat sequence of cell strings into as few terminal rows as possible."""
    if not cells:
        return []
    col_w = max(text_width(c) for c in cells)
    per_row = max(1, (width + gap) // (col_w + gap))
    if columns_:
        per_row = max(1, min(per_row, columns_))
    out: List[str] = []
    for i in range(0, len(cells), per_row):
        chunk = cells[i:i + per_row]
        out.append((" " * gap).join(pad(c, col_w) for c in chunk).rstrip())
    return out
