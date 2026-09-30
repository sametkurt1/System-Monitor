"""Terminal abstraction: VT enablement, size, non-blocking input, diff painting.

The renderer never clears the whole screen on a normal update.  It keeps the previous
frame and rewrites only the lines whose content actually changed, so a 1 Hz refresh of a
mostly-static dashboard does not flicker.
"""

from __future__ import annotations

import os
import sys
from typing import List, Optional, Sequence, Tuple

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as w

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class _SMALL_RECT(ctypes.Structure):
        _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                    ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]

    class _CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", _COORD),
            ("dwCursorPosition", _COORD),
            ("wAttributes", w.WORD),
            ("srWindow", _SMALL_RECT),
            ("dwMaximumWindowSize", _COORD),
        ]

    _k32.GetConsoleScreenBufferInfo.argtypes = [
        w.HANDLE, ctypes.POINTER(_CONSOLE_SCREEN_BUFFER_INFO)]
    _k32.GetConsoleScreenBufferInfo.restype = w.BOOL
    _k32.GetConsoleMode.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    _k32.GetConsoleMode.restype = w.BOOL
    _k32.SetConsoleMode.argtypes = [w.HANDLE, w.DWORD]
    _k32.SetConsoleMode.restype = w.BOOL
    _k32.SetConsoleOutputCP.argtypes = [w.UINT]
    _k32.SetConsoleOutputCP.restype = w.BOOL
    _k32.SetConsoleCP.argtypes = [w.UINT]
    _k32.SetConsoleCP.restype = w.BOOL

    STD_OUTPUT_HANDLE = ctypes.c_void_p(-11)
    STD_INPUT_HANDLE = ctypes.c_void_p(-10)
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    ENABLE_PROCESSED_OUTPUT = 0x0001
    ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
    ENABLE_WINDOW_INPUT = 0x0008
    CP_UTF8 = 65001

# Fallback box glyphs when the host cannot be coaxed into UTF-8 + VT.
UNICODE_BOX = {
    "tl": "\u256d", "tr": "\u256e", "bl": "\u2570", "br": "\u256f",
    "h": "\u2500", "v": "\u2502",
    "lt": "\u251c", "rt": "\u2524", "tt": "\u252c", "bt": "\u2534", "x": "\u253c",
    "dot": "\u00b7", "arrow": "\u2192", "check": "\u2713", "cross": "\u2717",
}
ASCII_BOX = {
    "tl": "+", "tr": "+", "bl": "+", "br": "+",
    "h": "-", "v": "|",
    "lt": "+", "rt": "+", "tt": "+", "bt": "+", "x": "+",
    "dot": ".", "arrow": "->", "check": "OK", "cross": "X",
}


class Console:
    """Owns the terminal: mode setup, size, input polling and painting."""

    def __init__(self, stream=None) -> None:
        self.out = stream or sys.stdout
        self.unicode_ok = True
        self.vt_ok = False
        self._prev_mode: Optional[int] = None
        self._prev_lines: List[str] = []
        self._alt_screen = False
        self._hidden_cursor = False
        self._last_size: Tuple[int, int] = (0, 0)
        self._need_full_repaint = True
        self._stdout_handle = None
        if sys.platform == "win32":
            try:
                import msvcrt
                self._msvcrt = msvcrt
            except Exception:  # pragma: no cover
                self._msvcrt = None
        else:
            self._msvcrt = None

    # ------------------------------------------------------------------ setup

    def setup(self, use_alt_screen: bool = True) -> None:
        """Enable UTF-8 and virtual-terminal processing, and optionally alt screen."""
        if sys.platform != "win32":
            self.unicode_ok = True
            self.vt_ok = True
            self._enter_modes(use_alt_screen)
            return

        # UTF-8 output so box drawing survives a legacy CMD code page.
        try:
            _k32.SetConsoleOutputCP(CP_UTF8)
            _k32.SetConsoleCP(CP_UTF8)
        except Exception:
            pass
        self.unicode_ok = self._probe_unicode()

        handle = self._handle(STD_OUTPUT_HANDLE)
        self._stdout_handle = handle
        mode = w.DWORD(0)
        if handle and _k32.GetConsoleMode(handle, ctypes.byref(mode)):
            self._prev_mode = int(mode.value)
            new = int(mode.value)
            new |= ENABLE_VIRTUAL_TERMINAL_PROCESSING
            new |= ENABLE_PROCESSED_OUTPUT
            if _k32.SetConsoleMode(handle, new):
                self.vt_ok = True
            else:
                # Fall back to the old style: still works, we just emit no escapes.
                self.vt_ok = False

        in_handle = self._handle(STD_INPUT_HANDLE)
        if in_handle:
            imode = w.DWORD(0)
            if _k32.GetConsoleMode(in_handle, ctypes.byref(imode)):
                new = int(imode.value)
                new |= ENABLE_WINDOW_INPUT
                _k32.SetConsoleMode(in_handle, new)

        self._enter_modes(use_alt_screen)

    def _probe_unicode(self) -> bool:
        """Confirm the stream can encode our glyphs, without emitting them."""
        test = "\u256d\u2500\u2502\u2588\u2591\u00b0\u2192"
        enc = getattr(self.out, "encoding", None)
        if enc is None:
            return True
        try:
            test.encode(enc)
            return True
        except (UnicodeEncodeError, LookupError):
            pass
        # The stream cannot represent them: try to switch it to UTF-8.
        reconfigure = getattr(self.out, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
                test.encode("utf-8")
                return True
            except Exception:
                return False
        return False

    def _enter_modes(self, use_alt_screen: bool) -> None:
        if not self.vt_ok:
            return
        if use_alt_screen:
            self.write("\x1b[?1049h")
            self._alt_screen = True
        self.write("\x1b[?25l")  # hide cursor
        self._hidden_cursor = True
        self.write("\x1b[2J\x1b[H")
        self._need_full_repaint = True

    def teardown(self) -> None:
        """Restore the terminal exactly as we found it."""
        if not self.vt_ok:
            try:
                self.out.write("\n")
                self.out.flush()
            except Exception:
                pass
            return
        self.write("\x1b[0m")
        if self._hidden_cursor:
            self.write("\x1b[?25h")
            self._hidden_cursor = False
        if self._alt_screen:
            self.write("\x1b[?1049l")
            self._alt_screen = False
        if self._prev_mode is not None and self._stdout_handle is not None:
            try:
                _k32.SetConsoleMode(self._stdout_handle, self._prev_mode)
            except Exception:
                pass
        try:
            self.out.flush()
        except Exception:
            pass

    # ------------------------------------------------------------------- size

    def size(self) -> Tuple[int, int]:
        """Return ``(columns, rows)`` of the visible window, min 40x10."""
        if sys.platform == "win32":
            handle = self._stdout_handle or self._handle(STD_OUTPUT_HANDLE)
            info = _CONSOLE_SCREEN_BUFFER_INFO()
            if handle and _k32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
                cols = info.srWindow.Right - info.srWindow.Left + 1
                rows = info.srWindow.Bottom - info.srWindow.Top + 1
                cols, rows = max(cols, 1), max(rows, 1)
                self._last_size = (cols, rows)
                return max(cols, 40), max(rows, 10)
        try:
            cols, rows = os.get_terminal_size()
        except Exception:
            cols, rows = self._last_size or (100, 30)
        self._last_size = (cols, rows)
        return max(cols, 40), max(rows, 10)

    def invalidate(self) -> None:
        """Force a full repaint on the next render (used after a resize)."""
        self._need_full_repaint = True
        self._prev_lines = []

    # ------------------------------------------------------------------ paint

    def write(self, s: str) -> None:
        try:
            self.out.write(s)
        except UnicodeEncodeError:
            enc = getattr(self.out, "encoding", None) or "ascii"
            try:
                self.out.write(s.encode(enc, errors="replace").decode(enc))
            except Exception:
                pass

    def render(self, lines: Sequence[str]) -> None:
        """Paint ``lines``, rewriting only what changed since the last frame."""
        if not self.vt_ok:
            # No cursor addressing available: repaint by scrolling, as CMD requires.
            self.write("\n".join(lines) + "\n")
            self._prev_lines = []
            self._need_full_repaint = True
            return

        out: List[str] = []
        prev = self._prev_lines
        if self._need_full_repaint or len(prev) != len(lines):
            out.append("\x1b[2J\x1b[H")
            self._need_full_repaint = False
            for i, ln in enumerate(lines):
                out.append(f"\x1b[{i + 1};1H\x1b[K{ln}")
            # Erase leftovers from a taller previous frame.
            for i in range(len(lines), len(prev)):
                out.append(f"\x1b[{i + 1};1H\x1b[K")
        else:
            for i, ln in enumerate(lines):
                if prev[i] != ln:
                    out.append(f"\x1b[{i + 1};1H\x1b[K{ln}")
            for i in range(len(lines), len(prev)):
                out.append(f"\x1b[{i + 1};1H\x1b[K")
        if out:
            # Park the cursor on the last row, out of the way of any scrollback.
            out.append(f"\x1b[{len(lines)};1H")
            self.write("".join(out))
            try:
                self.out.flush()
            except Exception:
                pass
        self._prev_lines = list(lines)

    def clear(self) -> None:
        self.write("\x1b[2J\x1b[H")
        self._prev_lines = []
        self._need_full_repaint = True

    # ------------------------------------------------------------------ input

    def poll_key(self) -> Optional[str]:
        """Return a key token, or ``None`` if nothing is waiting.

        Returns printable characters as-is, and named tokens for the special keys:
        ``up``, ``down``, ``left``, ``right``, ``home``, ``end``, ``pgup``, ``pgdn``,
        ``f1``..``f12``.  Control characters are reported as ``ctrl-<letter>``.
        """
        if self._msvcrt is None:
            return None
        try:
            if not self._msvcrt.kbhit():
                return None
            ch = self._msvcrt.getwch()
        except Exception:
            return None

        if ch in ("\x00", "\xe0"):
            try:
                if not self._msvcrt.kbhit():
                    return None
                code = self._msvcrt.getwch()
            except Exception:
                return None
            return _SCAN_CODES.get(code)
        if ch == "\x03":
            return "ctrl-c"
        if ch == "\x1b":
            return "esc"
        if ord(ch) < 32:
            return f"ctrl-{chr(ord(ch) + 96)}"
        return ch

    @staticmethod
    def _handle(value) -> Optional[int]:
        try:
            import msvcrt
            return int(msvcrt.get_osfhandle(value))
        except Exception:
            return None


_SCAN_CODES = {
    "H": "up", "P": "down", "K": "left", "M": "right",
    "G": "home", "O": "end",
    "I": "pgup", "Q": "pgdn",
    "R": "f2", "S": "f3",
    "\x8f": "f1", "\x90": "f2", "\x91": "f3", "\x92": "f4", "\x93": "f5",
    "\x94": "f6", "\x95": "f7", "\x96": "f8", "\x97": "f9", "\x98": "f10",
    "\x99": "f11", "\x9a": "f12",
}
