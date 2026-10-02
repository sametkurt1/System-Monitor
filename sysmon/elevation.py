"""Start-up plumbing: always run elevated, never show a console window.

sysmon needs Administrator rights for three things Windows refuses to hand out
otherwise:

  * ``PresentMon``/ETW frame capture, i.e. the in-game FPS counter;
  * ``LibreHardwareMonitor``'s kernel driver, i.e. CPU package temperature;
  * purging the standby list / system file cache.

Rather than making the user hunt for "Run as administrator", every entry point
runs this module first.  It re-launches the process through UAC (``runas``) when
needed and, when running under ``python.exe``, hands over to ``pythonw.exe`` so
no console window ever flashes up.

Flags understood here (all stripped before the real argument parser sees them):

  ``--elevated``     set on the copy that UAC produced; stops any re-elevation
                     loop if the token somehow did not stick.
  ``--no-admin``     escape hatch: start without requesting elevation.
  ``--no-relaunch``  skip the ``pythonw`` hand-over (used by the tests).
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from typing import List, Optional, Sequence, Tuple

FLAG_ELEVATED = "--elevated"
FLAG_NO_ADMIN = "--no-admin"
FLAG_NO_RELAUNCH = "--no-relaunch"

_INTERNAL_FLAGS = frozenset({FLAG_ELEVATED, FLAG_NO_ADMIN, FLAG_NO_RELAUNCH})

_ERROR_CANCELLED = 1223  # user pressed "No" on the UAC prompt
_SW_SHOWNORMAL = 1
_DETACHED_PROCESS = 0x00000008
_CREATE_NO_WINDOW = 0x08000000


def is_admin() -> bool:
    """True when the current process already holds the Administrator token."""
    if sys.platform != "win32":
        return False
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


# --------------------------------------------------------------------- Windows

class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("fMask", ctypes.c_ulong),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hKeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_ulong),
        ("hIconOrMonitor", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


def _shell_execute(verb: str, file: str, params: str, directory: str,
                   show: int) -> Tuple[bool, int]:
    """Run ``file`` through the shell with ``verb``.  Returns (ok, win32 error)."""
    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    info.fMask = 0
    info.hwnd = None
    info.lpVerb = verb
    info.lpFile = file
    info.lpParameters = params or None
    info.lpDirectory = directory or None
    info.nShow = show

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if shell32.ShellExecuteExW(ctypes.byref(info)):
        return True, 0
    return False, kernel32.GetLastError()


# ------------------------------------------------------------------- re-launch

def _pythonw_path() -> Optional[str]:
    exe = getattr(sys, "executable", "") or ""
    if not exe:
        return None
    candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return candidate if os.path.isfile(candidate) else None


def _script_path() -> str:
    """Absolute path of the entry script, even when launched via a shortcut."""
    try:
        return os.path.abspath(sys.argv[0] or __file__)
    except Exception:
        return os.path.abspath(__file__)


def _elevated_target(argv: Sequence[str]) -> Tuple[str, str]:
    """Build the (executable, parameter string) for the elevated copy.

    ``pythonw`` is preferred over ``python`` so the UAC hand-off does not leave a
    console window behind either.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, subprocess.list2cmdline(list(argv[1:]))

    exe = _pythonw_path() or sys.executable
    return exe, subprocess.list2cmdline([_script_path(), *argv[1:]])


def relaunch_elevated(argv: Sequence[str]) -> Tuple[bool, int]:
    """Ask UAC for elevation and start a privileged copy of this program."""
    exe, params = _elevated_target(argv)
    directory = os.path.dirname(os.path.abspath(exe)) or os.getcwd()
    return _shell_execute("runas", exe, params, directory, _SW_SHOWNORMAL)


def _relaunch_windowless(argv: Sequence[str]) -> bool:
    """Hand over to ``pythonw`` so the console window disappears immediately."""
    pythonw = _pythonw_path()
    if not pythonw:
        return False
    params = subprocess.list2cmdline([_script_path(), *argv[1:]])
    try:
        subprocess.Popen(
            [pythonw, _script_path(), *argv[1:]],
            creationflags=_DETACHED_PROCESS | _CREATE_NO_WINDOW,
            close_fds=True,
        )
    except Exception:
        # Last resort: let the shell detach it for us.
        try:
            return _shell_execute("open", pythonw, params, os.path.dirname(pythonw), 0)[0]
        except Exception:
            return False
    return True


def _hide_console() -> None:
    """Minimise an inherited console window (harmless when there is none)."""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


# ------------------------------------------------------------------ public API

def prepare_startup(argv: Sequence[str], windowless: bool = True) -> Optional[List[str]]:
    """Normalise ``argv`` for an elevated, console-free start.

    Returns the argument list the caller should keep running with, or ``None``
    when a replacement process was started and the caller must exit at once.

    ``windowless=False`` keeps the console (the terminal UI needs stdout) but
    still performs the UAC hand-off.
    """
    raw = list(argv)
    was_elevated = FLAG_ELEVATED in raw
    no_admin = FLAG_NO_ADMIN in raw
    no_relaunch = FLAG_NO_RELAUNCH in raw
    clean = [a for a in raw if a not in _INTERNAL_FLAGS]

    if sys.platform != "win32":
        return clean

    # The elevated worker is spawned by an already-elevated parent and must
    # never trigger another UAC round trip.
    if clean[:1] == ["--thermal-worker"]:
        return clean

    if not (no_admin or was_elevated) and not is_admin():
        args = clean + [FLAG_ELEVATED, FLAG_NO_RELAUNCH]
        try:
            ok, err = relaunch_elevated(args)
        except Exception:
            ok, err = False, 0
        if ok:
            return None
        if err == _ERROR_CANCELLED:
            sys.stderr.write(
                "sysmon: administrator permission was declined - CPU temperature and "
                "in-game FPS will stay unavailable.\n")
        elif err:
            sys.stderr.write(
                f"sysmon: could not request administrator rights (error {err}) - "
                "starting with limited sensors.\n")

    is_windowless = os.path.basename(getattr(sys, "executable", "")).lower() == "pythonw.exe"
    if windowless:
        if not is_windowless and not no_relaunch:
            if _relaunch_windowless(clean + [FLAG_NO_RELAUNCH]):
                return None
        _hide_console()

    return clean