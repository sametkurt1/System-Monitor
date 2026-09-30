"""Small shared helpers: width-aware text measurement and number formatting."""

from __future__ import annotations

import unicodedata
from typing import Optional

# East Asian Wide / Fullwidth occupy two terminal cells; combining marks occupy none.
_WIDE = frozenset("WF")

# Set once at start-up by the layout.  When the host console cannot represent
# non-ASCII code points (a legacy CMD on a non-UTF-8 code page), every helper falls
# back to plain ASCII so the dashboard degrades to something readable instead of
# a row of replacement characters.
ASCII_MODE = False

ELLIPSIS = "~"
DEGREE = "\u00b0"


def set_ascii_mode(enabled: bool) -> None:
    global ASCII_MODE, ELLIPSIS, DEGREE
    ASCII_MODE = bool(enabled)
    ELLIPSIS = "~" if ASCII_MODE else "\u2026"
    DEGREE = "" if ASCII_MODE else "\u00b0"


def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    if ch in ("\u200b",):
        return 0
    if unicodedata.east_asian_width(ch) in _WIDE:
        return 2
    if ch == "\t":
        return 8
    if ord(ch) < 32:
        return 0
    return 1


def text_width(s: str) -> int:
    """Display width of ``s`` in terminal cells, ignoring ANSI escape sequences."""
    if "\x1b" in s:
        s = strip_ansi(s)
    return sum(char_width(c) for c in s)


def strip_ansi(s: str) -> str:
    out = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "\x1b":
            j = i + 1
            if j < n and s[j] == "[":
                j += 1
                while j < n and not ("@" <= s[j] <= "~"):
                    j += 1
                i = j + 1
                continue
            i += 1
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def pad(s: str, width: int, align: str = "left", fill: str = " ") -> str:
    """Pad to ``width`` *display* cells, truncating with an ellipsis if too long."""
    if align not in ("left", "right", "center"):
        raise ValueError(f"bad align {align!r}")
    w = text_width(s)
    if w > width:
        if width <= 0:
            return ""
        if width == 1:
            return ELLIPSIS
        # Trim from the right, keeping ANSI sequences intact.
        out, cur = [], 0
        i, n = 0, len(s)
        while i < n and cur < width - 1:
            if s[i] == "\x1b":
                j = i + 1
                if j < n and s[j] == "[":
                    j += 1
                    while j < n and not ("@" <= s[j] <= "~"):
                        j += 1
                    j += 1
                else:
                    j = i + 1
                out.append(s[i:j])
                i = j
                continue
            cw = char_width(s[i])
            if cur + cw > width - 1:
                break
            out.append(s[i])
            cur += cw
            i += 1
        return "".join(out) + ELLIPSIS
    if align == "left":
        return s + fill * (width - w)
    if align == "right":
        return fill * (width - w) + s
    left = (width - w) // 2
    return fill * left + s + fill * (width - w - left)


def human_bytes(n: Optional[float], precision: int = 1) -> str:
    """Format a byte count using binary units, e.g. ``15.9 GiB``."""
    if n is None:
        return "N/A"
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(n) < 1024.0 or unit == "PiB":
            if unit == "B":
                return f"{int(n)} {unit}"
            return f"{n:.{precision}f} {unit}"
        n /= 1024.0
    return "N/A"


def human_gb(n: Optional[float], precision: int = 1) -> str:
    """Format a byte count as GB, which is what monitor dashboards usually show."""
    if n is None:
        return "N/A"
    return f"{float(n) / (1024 ** 3):.{precision}f} GB"


def human_watt(w: Optional[float]) -> str:
    if w is None:
        return "N/A"
    if w >= 100:
        return f"{w:.0f} W"
    if w >= 10:
        return f"{w:.1f} W"
    return f"{w:.1f} W"


def human_temp(c: Optional[float]) -> str:
    if c is None:
        return "N/A"
    if ASCII_MODE:
        return f"{c:.0f} C"
    return f"{c:.0f} {DEGREE}C"


def human_freq(mhz: Optional[float]) -> str:
    if mhz is None:
        return "N/A"
    if mhz >= 1000:
        return f"{mhz / 1000.0:.2f} GHz"
    return f"{mhz:.0f} MHz"


def pct(v: Optional[float], width: int = 0, decimals: int = 0) -> str:
    if v is None:
        return "N/A"
    s = f"{v:.{decimals}f}%"
    return pad(s, width, "right") if width else s


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return lo if v < lo else hi if v > hi else v
