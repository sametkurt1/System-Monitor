"""Composes a snapshot into terminal lines.

Layout adapts to the available width and height:

* wide enough -> CPU and MEMORY side by side, GPU full width underneath, per-core grid
  in its own panel.
* narrow or short -> the panels stack, and the per-core grid is dropped or trimmed.

Every value that is ``None`` renders as ``N/A``; nothing is ever substituted.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List, Optional, Sequence

from .formatting import (human_freq, human_gb, human_temp, human_watt, pad,
                         set_ascii_mode)
from .models import Snapshot
from .theme import (FG_ACCENT, FG_DIM, FG_TEXT, FG_TITLE, FG_VALUE, Style,
                     temp_color)
from .widgets import Gauge, Panel, bar_with_label, core_bar, gauge, grid, kv

CATEGORIES = ("CPU", "MEMORY", "GPU")

# Below this width the CPU and MEMORY panels stack instead of sitting side by side.
SIDE_BY_SIDE_MIN_WIDTH = 76


class History:
    """Bounded rolling history, used for the inline trend lines."""

    def __init__(self, size: int = 64) -> None:
        self.size = size
        self._data: Dict[str, Deque[Optional[float]]] = {}

    def push(self, key: str, value: Optional[float]) -> None:
        d = self._data.get(key)
        if d is None:
            d = deque(maxlen=self.size)
            self._data[key] = d
        d.append(value)

    def get(self, key: str) -> List[Optional[float]]:
        return list(self._data.get(key, ()))

    def prime(self, keys: Sequence[str], value: Optional[float]) -> None:
        for k in keys:
            if k not in self._data:
                d = deque(maxlen=self.size)
                d.extend([None] * (self.size - 1))
                d.append(value)
                self._data[k] = d


class Layout:
    def __init__(self, style: Style, glyphs, show_cores: bool = True,
                 enabled: Optional[set] = None) -> None:
        self.style = style
        self.glyphs = glyphs
        self.show_cores = show_cores
        # Categories whose provider was switched off.  Their panels are omitted
        # entirely rather than drawn as a column of N/A.
        self.enabled = set(enabled) if enabled else {"CPU", "MEMORY", "GPU"}
        self.history = History()
        self._core_scroll = 0
        # Make every formatting helper agree with the glyph set we can actually draw.
        set_ascii_mode(not glyphs.unicode_ok)

    def _on(self, category: str) -> bool:
        return category in self.enabled

    # --------------------------------------------------------------- helpers

    def _na(self) -> str:
        return self.style.na()

    def _val(self, text: str, ok: bool) -> str:
        return self.style.value(text) if ok else self._na()

    def core_page(self, delta: int, max_scroll: int) -> int:
        self._core_scroll = max(0, min(max_scroll, self._core_scroll + delta))
        return self._core_scroll

    def scroll_cores(self) -> int:
        return self._core_scroll

    # ---------------------------------------------------------------- panels

    def cpu_panel(self, snap: Snapshot, width: int, compact: bool = False) -> Panel:
        s, g = self.style, self.glyphs
        cpu = snap.cpu
        p = Panel("CPU", width, s, g, accent=FG_ACCENT)

        cores_desc = cpu.cores_logical or 0
        if cpu.cores_physical:
            cores_desc = f"{cpu.cores_physical}C/{cores_desc}T"
        elif cores_desc:
            cores_desc = f"{cores_desc} threads"

        p.add(s.paint(pad(cpu.name, p.inner), FG_TEXT, bold=True))
        if cores_desc or cpu.base_clock_mhz:
            bits = [b for b in (cores_desc,
                                f"base {cpu.base_clock_mhz / 1000.0:.2f} GHz"
                                if cpu.base_clock_mhz else None) if b]
            p.add(s.dim("  ".join(bits)))
        p.add()

        # Overall usage gauge.
        p.add(bar_with_label(cpu.usage, p.inner - 6, s, g,
                             f"{cpu.usage:.0f}%" if cpu.usage is not None else "N/A"))
        if not compact:
            p.add(Gauge.spark(self.history.get("cpu"), min(20, p.inner), s, g))
        p.add()

        p.add(kv("Frequency", human_freq(cpu.frequency_mhz) if cpu.has_frequency
                 else (f"N/A (base {cpu.base_clock_mhz / 1000.0:.2f} GHz)"
                       if cpu.base_clock_mhz else "N/A"), s,
                 value_color=None if cpu.has_frequency else FG_DIM))
        p.add(kv("Max/Boost", human_freq(cpu.frequency_max_mhz), s,
                 value_color=None if cpu.frequency_max_mhz else FG_DIM))
        p.add(kv("Temperature", human_temp(cpu.temperature_c), s,
                 value_color=temp_color(cpu.temperature_c) if cpu.has_temperature else FG_DIM))
        p.add(kv("Power", human_watt(cpu.power_w), s,
                 value_color=None if cpu.has_power else FG_DIM))
        if not compact and not (cpu.has_temperature or cpu.has_power or cpu.has_frequency):
            p.add(s.dim("no Windows API for these; see `sysmon diagnose`"))
        return p

    def memory_panel(self, snap: Snapshot, width: int, compact: bool = False) -> Panel:
        s, g = self.style, self.glyphs
        mem = snap.memory
        p = Panel("MEMORY", width, s, g, accent=(86, 156, 214))

        p.add(s.paint(pad(f"{human_gb(mem.total_bytes)} total", p.inner), FG_TEXT, bold=True))
        p.add()
        p.add(bar_with_label(mem.usage, p.inner - 6, s, g,
                             f"{mem.usage:.0f}%" if mem.usage is not None else "N/A"))
        if not compact:
            p.add(Gauge.spark(self.history.get("mem"), min(20, p.inner), s, g))
        p.add()
        p.add(kv("Used", human_gb(mem.used_bytes), s))
        p.add(kv("Available", human_gb(mem.available_bytes), s))
        if mem.commit_usage is not None:
            p.add(kv("Committed", f"{human_gb(mem.committed_bytes)} / "
                                  f"{human_gb(mem.commit_limit_bytes)}", s))
            if not compact:
                p.add(kv("Commit load", f"{mem.commit_usage:.0f}%", s))
        return p

    def gpu_panel(self, snap: Snapshot, width: int, gpu=None, compact: bool = False) -> Panel:
        s, g = self.style, self.glyphs
        gpus = list(snap.gpus)
        if gpu is not None:
            match = [x for x in gpus if x.index == gpu]
            if match:
                gpus = match
        p = Panel("GPU", width, s, g, accent=(170, 160, 255))

        if not gpus:
            p.add(s.dim("No NVIDIA GPU detected (NVML unavailable or no NVIDIA hardware)"))
            return p

        info = gpus[0]
        p.add(s.paint(pad(info.name, p.inner), FG_TEXT, bold=True))
        p.add()

        p.add(bar_with_label(info.usage, p.inner - 6, s, g,
                             f"{info.usage:.0f}%" if info.usage is not None else "N/A"))
        if not compact:
            p.add(Gauge.spark(self.history.get("gpu"), min(20, p.inner), s, g))
        p.add()

        half = (p.inner - 3) // 2

        def two(a_label, a_value, b_label, b_value, a_color=None):
            left = kv(a_label, a_value, s, label_width=13, value_color=a_color)
            right = kv(b_label, b_value, s, label_width=13)
            return pad(left, half) + "   " + right

        p.add(two("Temperature", human_temp(info.temperature_c),
                  "Power", human_watt(info.power_w),
                  temp_color(info.temperature_c) if info.temperature_c is not None else None))
        p.add(two("Core Clock", human_freq(info.clock_mhz),
                  "Mem Clock", human_freq(info.clock_memory_mhz)))
        if info.clock_max_mhz and not compact:
            p.add(two("Max Clock", human_freq(info.clock_max_mhz),
                      "Max Mem", human_freq(info.clock_memory_max_mhz)))
        p.add(kv("VRAM", f"{human_gb(info.memory_used_bytes, 2)} / "
                         f"{human_gb(info.memory_total_bytes, 1)}"
                         + (f"  ({info.memory_usage:.0f}%)" if info.memory_usage is not None else ""),
                 s,
                 value_color=None if info.memory_used_bytes is not None else FG_DIM))
        p.add(bar_with_label(info.memory_usage, p.inner - 6, s, g,
                             f"{info.memory_usage:.0f}%" if info.memory_usage is not None else "N/A"))
        if info.fan_percent is not None and not compact:
            p.add(s.dim(f"fan {info.fan_percent}%"))
        if len(gpus) > 1:
            p.add()
            for other in gpus[1:]:
                p.add(s.label(pad(f"GPU{other.index}", 5)) + " " +
                      bar_with_label(other.usage, max(10, p.inner - 24), s, g,
                                     f"{other.usage:.0f}%" if other.usage is not None else "N/A"))
        return p

    def cores_panel(self, snap: Snapshot, width: int, cols: int, max_rows: int,
                    per_page: int) -> Panel:
        s, g = self.style, self.glyphs
        p = Panel("CPU CORES", width, s, g, accent=FG_ACCENT)
        cores = list(snap.cpu.per_core)

        if not cores:
            p.add(s.dim("Per-core data unavailable"))
            return p

        # Fit the bar to the space the column count leaves over.
        label_w = 3
        pct_w = 4
        overhead = cols * (label_w + 1 + pct_w + 1) + (cols - 1) * 2
        bar_w = max(4, min(16, (width - 4 - overhead) // max(1, cols)))

        total_pages = max(1, (len(cores) + per_page - 1) // per_page)
        page = min(self._core_scroll, total_pages - 1)
        visible = cores[page * per_page:(page + 1) * per_page]

        cells = [core_bar(c.index, c.usage, bar_w, s, g) for c in visible]
        body = grid(cells, columns_=cols, width=width, style=s, glyphs=g)
        for line in body[:max_rows]:
            p.add(line)

        footer = f"{len(cores)} logical processors"
        if total_pages > 1:
            footer += f"   page {page + 1}/{total_pages}  ({g.arrow} scroll)"
        p.footer.append(s.dim(footer))
        return p

    def _core_columns(self, width: int) -> int:
        if width >= 116:
            return 4
        if width >= 78:
            return 3
        if width >= 54:
            return 2
        return 1

    # ----------------------------------------------------------------- frame

    def build(self, snap: Snapshot, width: int, height: int, footer: str,
              paused: bool = False) -> List[str]:
        """Compose the body.

        Tiers are tried in order and the first that fits wins:
          1. full       - everything, including the per-core grid
          2. compact    - no sparklines or secondary rows
          3. minimal    - one borderless line per category, so all three
                          categories stay visible on a very short terminal
        """
        self.history.push("cpu", snap.cpu.usage)
        self.history.push("mem", snap.memory.usage)
        self.history.push("gpu", snap.gpus[0].usage if snap.gpus else None)

        # Two rows go to the outer frame, one to the footer.
        budget = max(3, height - 3)

        tiers = ((False, self.show_cores), (True, self.show_cores), (True, False))
        fallback: Optional[List[str]] = None
        for compact, show_cores in tiers:
            body, pages = self._compose(snap, width, budget, compact, show_cores)
            if len(body) > budget:
                continue
            if fallback is None:
                fallback = body
            # Prefer the least compact tier that still shows every core at once;
            # sparklines are worth less than an unpaged core grid.
            if pages <= 1:
                return body
        return fallback if fallback is not None else self._minimal(snap, width)[:budget]

    def _minimal(self, snap: Snapshot, width: int) -> List[str]:
        """One dense line per category - the last-resort layout."""
        s, g = self.style, self.glyphs
        lines: List[str] = []

        def row(label: str, usage: Optional[float], extra: str) -> str:
            head = s.paint(pad(label, 8), FG_TITLE, bold=True)
            bar_w = max(4, min(16, width - 30))
            return head + gauge(usage, bar_w, s, g) + " " + extra

        cpu = snap.cpu
        if self._on("CPU"):
            bits = []
            if cpu.has_temperature:
                bits.append(human_temp(cpu.temperature_c))
            if cpu.has_power:
                bits.append(human_watt(cpu.power_w))
            if cpu.has_frequency:
                bits.append(human_freq(cpu.frequency_mhz))
            name_w = max(0, width - 30)
            lines.append(row("CPU", cpu.usage,
                             s.paint(pad(cpu.name, name_w), FG_TEXT)
                             + ("  " + s.dim(" ".join(bits)) if bits else "")))

        mem = snap.memory
        if self._on("MEMORY"):
            lines.append(row("MEMORY", mem.usage,
                             s.paint(f"{human_gb(mem.used_bytes)} / {human_gb(mem.total_bytes)}",
                                     FG_VALUE)))

        if not self._on("GPU"):
            return lines
        if snap.gpus:
            gi = snap.gpus[0]
            gb = []
            if gi.temperature_c is not None:
                gb.append(human_temp(gi.temperature_c))
            if gi.power_w is not None:
                gb.append(human_watt(gi.power_w))
            lines.append(row("GPU", gi.usage,
                             s.paint(pad(gi.name, name_w), FG_TEXT)
                             + ("  " + s.dim(" ".join(gb)) if gb else "")))
        else:
            lines.append(s.dim(pad("GPU     no NVIDIA GPU detected", width)))
        return lines

    def _compose(self, snap: Snapshot, width: int, budget: int, compact: bool,
                 show_cores: bool) -> tuple:
        """Render one tier.  Returns ``(lines, core_pages)``."""
        body: List[str] = []
        pages = 1
        wide = width >= SIDE_BY_SIDE_MIN_WIDTH

        # Build the top row from whichever of CPU / MEMORY are enabled.
        top = []
        if self._on("CPU"):
            top.append("CPU")
        if self._on("MEMORY"):
            top.append("MEMORY")

        if len(top) == 2 and wide:
            half = (width - 4) // 2
            left = self.cpu_panel(snap, half, compact).render()
            right = self.memory_panel(snap, width - half - 4, compact).render()
            delta = len(left) - len(right)
            if delta > 0:
                right = right + [""] * delta
            elif delta < 0:
                left = left + [""] * (-delta)
            for i in range(max(len(left), len(right))):
                body.append(pad(left[i], half) + "  " + (right[i] if i < len(right) else ""))
        else:
            for category in top:
                panel = (self.cpu_panel(snap, width, compact) if category == "CPU"
                         else self.memory_panel(snap, width, compact))
                body.extend(panel.render())
                body.append("")

        if self._on("GPU"):
            body.append("")
            body.extend(self.gpu_panel(snap, width, compact=compact).render())

        if (self._on("CPU") and self.show_cores and snap.cpu.per_core):
            # Chrome around the grid: one blank separator row, the panel's two border
            # rows and its footer row.
            leftover = budget - len(body) - 4
            cols = self._core_columns(width)
            if leftover >= 3:
                max_rows = max(1, leftover - 2)      # rows the grid itself may use
                per_page = max(cols, cols * max_rows)
                pages = max(1, (len(snap.cpu.per_core) + per_page - 1) // per_page)
                self._core_scroll = min(self._core_scroll, pages - 1)
                body.append("")
                body.extend(self.cores_panel(snap, width, cols, max_rows,
                                             per_page).render())
        return body, pages
