"""The main loop: sample, render, paint, handle keys.

Design points:
  * Sampling is decoupled from painting.  The collector runs on a fixed cadence; the
    renderer runs on the same cadence but only rewrites lines that changed.
  * Keyboard input is polled non-blockingly, so no key is ever "stuck" and CPU usage
    stays negligible.
  * Every exit path (q, Esc, Ctrl+C, unhandled exception, terminal resize) restores the
    console and releases hardware-monitoring resources.
"""

from __future__ import annotations

import signal
import time
from typing import Optional

from .collector import Collector
from .console import Console
from .formatting import pad, text_width
from .layout import Layout
from .models import Snapshot
from .theme import Style
from .widgets import Glyphs

MIN_INTERVAL = 0.10
MAX_INTERVAL = 10.0

HELP_LINES = [
    "q quit",
    "+/- interval",
    "p pause",
    "c cores",
    "pgup/pgdn cores",
    "up/down gpu",
    "d diagnostics",
]


class App:
    def __init__(self, collector: Collector, interval: float = 1.0,
                 show_cores: bool = True, once: bool = False) -> None:
        self.collector = collector
        self.interval = max(MIN_INTERVAL, min(MAX_INTERVAL, interval))
        self.once = once
        self.console = Console()
        self.glyphs: Optional[Glyphs] = None
        self.layout: Optional[Layout] = None
        self._paused = False
        self._show_cores = show_cores
        self._show_help = True
        self._gpu_index = 0
        self._last_size = (0, 0)
        self._snapshot: Optional[Snapshot] = None
        self._next_sample = 0.0
        self._running = True
        self._drain = 0
        self.force_no_color = False
        self.use_alt_screen = True
        self.forced_size: Optional[tuple] = None
        self.exit_after: Optional[float] = None
        self._deadline: Optional[float] = None

    # ------------------------------------------------------------------ setup

    def _build_ui(self) -> None:
        color = self.console.vt_ok and not self.force_no_color
        self.glyphs = Glyphs(unicode_ok=self.console.unicode_ok and self.console.vt_ok)
        # Only render categories whose provider is actually running.
        enabled = self.collector.enabled_categories() if hasattr(
            self.collector, "enabled_categories") else None
        self.layout = Layout(Style(color=color), self.glyphs,
                             show_cores=self._show_cores, enabled=enabled)
        assert self.layout is not None
        self._style = Style(color=color)

    def _install_signals(self) -> None:
        def handler(signum, frame):
            self._running = False
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError, AttributeError):
                pass

    # ------------------------------------------------------------------- loop

    def run(self) -> int:
        self.console.setup(use_alt_screen=self.use_alt_screen and not self.once)
        try:
            self._build_ui()
            self._install_signals()
            self.collector.start()
            if self.once:
                self._render_once()
                return 0
            self.collector.warm(2)
            self._snapshot = self.collector.sample()
            return self._loop()
        except KeyboardInterrupt:
            return 0
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        # Order matters: leave the terminal before releasing the handles, so a slow
        # or failing driver unload can never leave the shell in the alt screen.
        try:
            self.console.teardown()
        except Exception:
            pass
        try:
            self.collector.stop()
        except Exception:
            pass

    def _loop(self) -> int:
        if self.exit_after is not None:
            self._deadline = time.monotonic() + self.exit_after
        while self._running:
            now = time.monotonic()
            if self._deadline is not None and now >= self._deadline:
                break
            if not self._paused and now >= self._next_sample:
                try:
                    snap = self.collector.sample()
                    if snap is not None:
                        self._snapshot = snap
                except Exception:
                    pass
                self._next_sample = now + self.interval
            self._paint()
            self._handle_keys()
            self._sleep()
        return 0

    def _sleep(self) -> None:
        """Sleep in short slices so keys, resizes and the exit deadline stay responsive."""
        while self._running:
            if self._deadline is not None:
                remaining = min(self._deadline, self._next_sample if not self._paused
                                else time.monotonic() + self.interval) - time.monotonic()
            else:
                remaining = (self._next_sample if not self._paused
                             else time.monotonic() + self.interval) - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.05, max(0.001, remaining)))

    # ----------------------------------------------------------------- render

    def _header(self, width: int, height: int) -> str:
        now = time.strftime("%H:%M:%S")
        interval = f"{self.interval:.2f}s" if self.interval >= 0.1 else f"{self.interval * 1000:.0f}ms"
        left = f"{width}x{height}"
        status = "PAUSED" if self._paused else f"refresh {interval}"
        return f"{left}   {status}   {now}"

    def _footer(self, width: int) -> str:
        """Footer hint line, trimmed to whatever actually fits."""
        s = self._style
        budget = max(10, width)
        if not self._show_help:
            return pad(s.dim("  q quit    d diagnostics"), budget)
        parts = HELP_LINES
        line = ""
        for item in parts:
            candidate = (line + "   " + item) if line else ("  " + item)
            if text_width(candidate) > budget:
                break
            line = candidate
        if not line:
            line = "  q quit"
        return pad(s.dim(line), budget)

    def _paint(self) -> None:
        assert self.layout is not None and self.glyphs is not None
        if self.forced_size is not None:
            width, height = self.forced_size
        else:
            width, height = self.console.size()
        if (width, height) != self._last_size:
            self._last_size = (width, height)
            self.console.invalidate()

        snap = self._snapshot or Snapshot()
        # The body sits inside the outer frame, whose inner width is two cells less.
        body_width = max(20, width - 2)
        try:
            body = self.layout.build(snap, body_width, height, self._footer(width),
                                     paused=self._paused)
        except Exception as exc:
            body = [f"layout error: {exc}"]

        from .widgets import frame
        lines = frame(body, width, self._style, self.glyphs,
                      title="SYSTEM MONITOR",
                      subtitle=self._header(width, height))
        # Reserve the last row for the footer; it is already width-fitted.
        lines = lines[:max(1, height - 1)]
        lines.append(self._footer(width))
        self.console.render(lines)

    def _render_once(self) -> None:
        """Print one static frame (used by ``--once`` and by the screenshot helper)."""
        self._build_ui()
        self.collector.start()
        self.collector.warm(2)
        self._snapshot = self.collector.sample()
        self._paint()
        time.sleep(0.15)

    # ------------------------------------------------------------------- keys

    def _handle_keys(self) -> None:
        for _ in range(16):
            key = self.console.poll_key()
            if key is None:
                return
            if not self._apply_key(key):
                return

    def _apply_key(self, key: str) -> bool:
        """Return ``False`` to stop reading further keys this tick."""
        if key in ("q", "Q", "esc", "ctrl-c", "ctrl-d", "ctrl-z"):
            self._running = False
            return False
        if key in ("+", "=", "ctrl-="):
            self.interval = min(MAX_INTERVAL, round(self.interval + 0.25, 2))
        elif key in ("-", "_"):
            self.interval = max(MIN_INTERVAL, round(self.interval - 0.25, 2))
        elif key == "p":
            self._paused = not self._paused
            self._next_sample = time.monotonic()
        elif key == "c":
            self._show_cores = not self._show_cores
            if self.layout is not None:
                self.layout.show_cores = self._show_cores
            self.console.invalidate()
        elif key == "h":
            self._show_help = not self._show_help
        elif key == "r":
            self.console.invalidate()
        elif key in ("left", "right", "up", "down"):
            self._scroll(key)
        elif key == "d":
            self._show_diagnostics()
        return True

    def _scroll(self, key: str) -> None:
        if self.layout is None:
            return
        if self.forced_size is not None:
            width, height = self.forced_size
        else:
            width, height = self.console.size()
        cores = self._snapshot.cpu.per_core if self._snapshot else ()
        cols = self.layout._core_columns(width)
        rows = max(1, (height - 26) // 1)
        per_page = max(cols, cols * max(1, rows))
        pages = max(0, (len(cores) - 1) // per_page) if cores else 0
        if key == "left":
            self.layout.core_page(-1, pages)
        elif key == "right":
            self.layout.core_page(1, pages)
        elif key in ("up", "down"):
            gpus = self._snapshot.gpus if self._snapshot else ()
            if gpus:
                self._gpu_index = (self._gpu_index + (1 if key == "down" else -1)) % len(gpus)
            del gpus

    def _show_diagnostics(self) -> None:
        """Pop the alternate screen, print sensor availability, then redraw."""
        self.console.write("\x1b[?1049l\x1b[2J\x1b[H")
        lines = self.diagnostic_lines()
        self.console.write("\n".join(lines) + "\n\n  Press any key to return... ")
        self.console.out.flush()
        self._drain = time.monotonic() + 0.2
        while time.monotonic() < self._drain:
            time.sleep(0.02)
        while self.console.poll_key() is None:
            time.sleep(0.03)
        self.console.write("\x1b[?1049h")
        self.console.invalidate()

    def diagnostic_lines(self):
        from .diagnose import report
        return report(self.collector)
