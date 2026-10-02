"""In-game FPS and frametime monitoring provider.

Frame timings come from Intel/NVIDIA `PresentMon`, which traces the DXGI/D3D9
present pipeline through ETW.  Three things have to be right for the readout to
be trustworthy:

  * **Elevation.**  PresentMon refuses to start its ETW session (and cannot even
    resolve process names) without Administrator rights, so it reports nothing at
    all rather than degraded numbers.
  * **Column layout.**  PresentMon ships two metric sets and builds that add
    debug columns, so the CSV header is parsed into an index map instead of
    assuming fixed column numbers - assuming them silently read
    ``AllowsTearing`` as the drop flag, which threw away nearly every frame.
  * **Timely rows.**  PresentMon only flushes stdout after each frame for the 1.x
    metric set; the 2.x set left the pipe block-buffered, so rows arrived in
    multi-second bursts.  ``--v1_metrics`` is requested when the build supports
    it, and frames are timestamped from PresentMon's own ``TimeInSeconds`` so a
    burst still yields a correct rate.

When sysmon itself is not elevated, the numbers can still arrive through the IPC
file written by an elevated sibling process (see ``thermal.run_thermal_worker``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from typing import Dict, List, NamedTuple, Optional, Tuple

from ..models import FpsSnapshot
from .base import Capability, CapabilityState, OK, unavailable

# Processes that present constantly but are never "the game": shell, desktop
# compositor, launchers, browsers.  Reporting a browser's compositor rate as
# in-game FPS is worse than showing nothing.
_IGNORED_PROCESSES = frozenset({
    "explorer.exe", "dwm.exe", "searchhost.exe", "searchui.exe",
    "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "peopleexperiencehost.exe", "taskhostw.exe", "taskmgr.exe",
    "systemsettings.exe", "lockapp.exe", "textinputhost.exe",
    "applicationframehost.exe", "sihost.exe", "fontdrvhost.exe",
    "ctfmon.exe", "widgets.exe", "widgetboard.exe", "smss.exe",
    "csrss.exe", "winlogon.exe", "lsass.exe", "services.exe",
    "svchost.exe", "screentoplayer.exe", "wallpaperengine.exe",
    # launchers / overlays / monitors
    "python.exe", "pythonw.exe", "sysmon.exe", "cmd.exe", "conhost.exe",
    "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe",
    "obs64.exe", "obs32.exe", "nvcontainer.exe", "nvidia app.exe",
    "nvidia overlay.exe", "nvsdkhelper.exe", "shadowplay.exe",
    "gamebar.exe", "gameoverlayui.exe", "gameoverlayrenderer.exe",
    "discord.exe", "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe",
    "battle.net.exe", "eabackgroundservice.exe", "origin.exe",
    "galaxyclient.exe", "upc.exe", "javaw.exe",
    # browsers present through the GPU compositor just like a game does
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "opera_gx.exe", "vivaldi.exe", "chromium.exe", "msedgewebview2.exe",
    "iexplore.exe", "tor.exe", "waterfox.exe", "librewolf.exe", "zen.exe",
})

_CREATE_NO_WINDOW = 0x08000000

# A PresentMon that dies faster than this never opened its ETW session.
_FAST_FAILURE_SECONDS = 3.0


def _is_ignored(app_name: Optional[str]) -> bool:
    if not app_name:
        return True
    base = os.path.basename(app_name).strip().lower()
    if not base:
        return True
    if base in _IGNORED_PROCESSES:
        return True
    return base.startswith(("nvcontainer", "windows explorer"))


def find_presentmon() -> Optional[str]:
    """Find the PresentMon executable path."""
    here = os.path.dirname(os.path.abspath(__file__))
    roots = [here]
    if getattr(sys, "frozen", False):
        roots.insert(0, os.path.dirname(os.path.abspath(sys.executable)))
    candidates: List[str] = []
    for root in roots:
        candidates.append(os.path.join(root, "PresentMon_x64.exe"))
        candidates.append(os.path.join(root, "PresentMon.exe"))
    candidates += [
        r"C:\Program Files\NVIDIA Corporation\FrameViewSDK\bin\PresentMon_x64.exe",
        r"C:\Program Files (x86)\NVIDIA Corporation\FrameViewSDK\bin\PresentMon_x64.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c

    for name in ("PresentMon_x64.exe", "PresentMon.exe"):
        which = shutil.which(name)
        if which:
            return which

    return None


_FLAG_SUPPORT: Dict[Tuple[str, str], bool] = {}


def supports_flag(exe: str, flag: str) -> bool:
    """Whether this PresentMon build accepts ``flag``.

    Probed once per (exe, flag) with ``--help``, which works without elevation
    and costs a fraction of a second.
    """
    key = (exe.lower(), flag.lower())
    cached = _FLAG_SUPPORT.get(key)
    if cached is not None:
        return cached
    ok = False
    try:
        proc = subprocess.run(
            [exe, "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
        text = (proc.stdout or b"").decode("utf-8", "replace").lower()
        ok = flag.lower() in text
    except Exception:
        ok = False
    _FLAG_SUPPORT[key] = ok
    return ok


def is_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def get_foreground_process() -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Return (pid, exe_name, window_title) of the active foreground window."""
    if sys.platform != "win32":
        return None, None, None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None, None, None

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == 0:
            return None, None, None

        exe_name = ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if h_proc:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
                exe_name = os.path.basename(buf.value)
            kernel32.CloseHandle(h_proc)

        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            title_buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buf, length + 1)
            title = title_buf.value

        return pid.value, exe_name, title
    except Exception:
        return None, None, None


# ------------------------------------------------------------------------ IPC

def get_ipc_fps_path() -> str:
    username = os.environ.get("USERNAME", "user")
    return os.path.join(tempfile.gettempdir(), f"sysmon_fps_{username}.json")


def write_ipc_fps(snap: FpsSnapshot) -> None:
    path = get_ipc_fps_path()
    tmp_path = path + f".{os.getpid()}.tmp"
    data = {
        "timestamp": time.time(),
        "fps": snap.fps,
        "frametime_ms": snap.frametime_ms,
        "fps_1percent_low": snap.fps_1percent_low,
        "app_name": snap.app_name,
        "pid": snap.pid,
        "is_active": snap.is_active,
        "is_available": snap.is_available,
        "detail": snap.detail,
    }
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def read_ipc_fps(max_age_s: float = 2.5) -> Optional[FpsSnapshot]:
    path = get_ipc_fps_path()
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        ts = data.get("timestamp", 0)
        if time.time() - ts > max_age_s:
            return None
        return FpsSnapshot(
            fps=data.get("fps"),
            frametime_ms=data.get("frametime_ms"),
            fps_1percent_low=data.get("fps_1percent_low"),
            app_name=data.get("app_name"),
            pid=data.get("pid"),
            is_active=bool(data.get("is_active", False)),
            is_available=bool(data.get("is_available", False)),
            detail=data.get("detail", ""),
        )
    except Exception:
        return None


# ------------------------------------------------------------------ CSV reader

class Frame(NamedTuple):
    app: str
    pid: int
    dropped: bool
    frametime_ms: Optional[float]
    timestamp: Optional[float]


# Logical field -> accepted CSV header spellings, best first.  The header is the
# only contract between sysmon and PresentMon; positions are not.
_FIELD_ALIASES = {
    "app": ("application",),
    "pid": ("processid",),
    "dropped": ("dropped",),
    "time": ("timeinseconds", "cpustarttime"),
    "frametime": ("msbetweenpresents", "frametime", "cpu busy"),
    "display_delta": ("msbetweendisplaychange",),
}


def _clean(name: str) -> str:
    return name.strip().strip('"').strip().lower()


class PresentMonCsvReader:
    """Converts PresentMon CSV lines into :class:`Frame` records."""

    def __init__(self) -> None:
        self._index: Dict[str, int] = {}
        self.header: str = ""
        self.rows = 0
        self.malformed = 0

    @property
    def ready(self) -> bool:
        return bool(self._index)

    def bind_header(self, fields: List[str]) -> None:
        names = [_clean(f) for f in fields]
        self._index = {}
        for key, aliases in _FIELD_ALIASES.items():
            for alias in aliases:
                if alias in names:
                    self._index[key] = names.index(alias)
                    break

    def feed(self, line: str) -> Optional[Frame]:
        if not line:
            return None
        line = line.strip().strip("\x00").strip()
        if not line:
            return None

        fields = line.split(",")
        if not self._index:
            if _clean(fields[0]) == "application":
                self.header = line
                self.bind_header(fields)
            return None

        pid_text = self._value(fields, "pid")
        try:
            pid = int(pid_text)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            self.malformed += 1
            return None
        if pid <= 0:
            self.malformed += 1
            return None

        app = self._value(fields, "app") or "Unknown"

        dropped_text = self._value(fields, "dropped")
        dropped = bool(int(dropped_text)) if dropped_text and dropped_text.isdigit() else False

        frametime = self._float(fields, "frametime")
        if not frametime or frametime <= 0:
            frametime = self._float(fields, "display_delta")
        if frametime is not None and frametime <= 0:
            frametime = None

        timestamp = self._float(fields, "time")
        self.rows += 1
        return Frame(app=app, pid=pid, dropped=dropped,
                     frametime_ms=frametime, timestamp=timestamp)

    def _value(self, fields: List[str], key: str) -> Optional[str]:
        idx = self._index.get(key)
        if idx is None or idx >= len(fields):
            return None
        value = fields[idx].strip().strip('"').strip()
        return value or None

    def _float(self, fields: List[str], key: str) -> Optional[float]:
        raw = self._value(fields, key)
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None


# --------------------------------------------------------------------- tracker

class FpsTracker:
    """Aggregates frame events and calculates FPS, frametime, and 1% low.

    Frames carry PresentMon's own ``TimeInSeconds`` so the rate stays exact even
    when rows arrive in bursts (a sign of stdout block-buffering on the far
    side), and a private wall-clock stamp is kept per process purely for
    liveness so a stalled game stops being reported.
    """

    #: A process must have produced a frame this recently to count as "running".
    LIVE_SECONDS = 2.0
    #: Below this span, counting frames is noise, so the average frametime wins.
    MIN_SPAN = 0.25

    def __init__(self, window_seconds: float = 1.0, low_window_seconds: float = 3.0,
                 max_samples: int = 900) -> None:
        self.window_seconds = window_seconds
        self.low_window_seconds = low_window_seconds
        self.max_samples = max_samples
        self.proc_frames: Dict[int, deque] = {}
        self.proc_names: Dict[int, str] = {}
        self.last_seen: Dict[int, float] = {}
        self._lock = threading.Lock()

    def add_frame(self, app_name: str, pid: int, dropped: bool = False,
                  frametime_ms: Optional[float] = None,
                  timestamp: Optional[float] = None) -> None:
        if dropped:
            return
        if not frametime_ms and timestamp is None:
            return
        wall = time.time()
        stamp = float(timestamp) if timestamp is not None else wall
        with self._lock:
            bucket = self.proc_frames.get(pid)
            if bucket is None:
                bucket = self.proc_frames[pid] = deque(maxlen=self.max_samples)
            bucket.append((stamp, frametime_ms))
            self.proc_names[pid] = app_name
            self.last_seen[pid] = wall

    def reset(self) -> None:
        with self._lock:
            self.proc_frames.clear()
            self.proc_names.clear()
            self.last_seen.clear()

    # ------------------------------------------------------------- statistics

    def compute_stats(self, target_pid: Optional[int] = None
                      ) -> Tuple[Optional[float], Optional[float], Optional[float],
                                 Optional[str], Optional[int], bool]:
        now = time.time()
        with self._lock:
            stale = [p for p, t in self.last_seen.items() if now - t > 5.0]
            for pid in stale:
                self.proc_frames.pop(pid, None)
                self.proc_names.pop(pid, None)
                self.last_seen.pop(pid, None)

            active = [p for p, t in self.last_seen.items() if now - t <= self.LIVE_SECONDS]
            if not active:
                return None, None, None, None, None, False

            chosen = self._choose(active, target_pid)
            if chosen is None:
                # Only shell/desktop/browser processes are presenting.  Reporting
                # any of them as "in-game FPS" is noise, not a measurement.
                return None, None, None, None, None, False

            frames = self.proc_frames.get(chosen)
            if not frames:
                return None, None, None, None, None, False

            app_name = self.proc_names.get(chosen, "Game")
            newest = frames[-1][0]
            window = [f for f in frames if newest - f[0] <= self.window_seconds]
            if not window:
                return None, None, None, app_name, chosen, False

            fps = self._rate(window)
            if fps is None or fps <= 0:
                return None, None, None, app_name, chosen, False

            frametime = self._last_frametime(window, fps)
            low_window = [f for f in frames if newest - f[0] <= self.low_window_seconds]
            low = self._percentile_low(low_window, fps)

            return round(fps, 1), frametime, low, app_name, chosen, True

    def _choose(self, active: List[int], target_pid: Optional[int]) -> Optional[int]:
        """Pick the process to report, or ``None`` when none of them qualify."""
        if target_pid is not None and target_pid in active:
            if not _is_ignored(self.proc_names.get(target_pid)):
                return target_pid
        candidates = [p for p in active if not _is_ignored(self.proc_names.get(p))]
        if not candidates:
            return None
        return max(candidates, key=lambda p: self.last_seen.get(p, 0.0))

    @staticmethod
    def _rate(window: List[Tuple[float, Optional[float]]]) -> Optional[float]:
        frametimes = [ft for _, ft in window if ft is not None and ft > 0]
        span = window[-1][0] - window[0][0]
        if len(window) >= 2 and span >= FpsTracker.MIN_SPAN:
            return (len(window) - 1) / span
        if frametimes:
            average = sum(frametimes) / len(frametimes)
            if average > 0:
                return 1000.0 / average
        if len(window) >= 2 and span > 0:
            return (len(window) - 1) / span
        return None

    @staticmethod
    def _last_frametime(window: List[Tuple[float, Optional[float]]],
                        fps: float) -> Optional[float]:
        for _, frametime in reversed(window):
            if frametime is not None and frametime > 0:
                return round(frametime, 2)
        return round(1000.0 / fps, 2) if fps > 0 else None

    @staticmethod
    def _percentile_low(frames: List[Tuple[float, Optional[float]]],
                        fps: float) -> float:
        """1% low = the frame time at the 99th percentile, inverted to FPS.

        Needs a decent sample before the worst frame stops being noise, so a
        short burst simply reports the current rate instead of a scary outlier.
        """
        values = sorted(ft for _, ft in frames if ft is not None and ft > 0)
        if len(values) < 10:
            return round(fps, 1)
        index = min(len(values) - 1, int(len(values) * 0.99))
        worst = values[index]
        if worst <= 0:
            return round(fps, 1)
        return round(1000.0 / worst, 1)


# ---------------------------------------------------------------------- source

class FpsSource:
    """In-game FPS provider using PresentMon, or an elevated worker's IPC feed."""

    name = "presentmon"
    SESSION = "SysmonFps"

    def __init__(self) -> None:
        self.tracker = FpsTracker()
        self.reader = PresentMonCsvReader()
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._started = False
        self._presentmon_exe: Optional[str] = None
        self._retry_at = 0.0
        self._failures = 0
        self._launched_at = 0.0
        self._fast_failures = 0
        self._last_snapshot = FpsSnapshot()

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._started:
            return
        self._stop.clear()
        self._presentmon_exe = find_presentmon()
        if self._presentmon_exe and is_admin():
            self._launch_presentmon()
        self._started = True

    def _launch_presentmon(self) -> bool:
        exe = self._presentmon_exe
        if not exe or not os.path.isfile(exe):
            return False

        cmd = [
            exe,
            "--session_name", self.SESSION,
            "--stop_existing_session",
            "--output_stdout",
            "--no_console_stats",
        ]
        # The 1.x metric set flushes stdout per frame; the 2.x one historically
        # did not, which made the counter jump in multi-second bursts.
        if supports_flag(exe, "--v1_metrics"):
            cmd.append("--v1_metrics")

        self._dispose_process()
        self.reader = PresentMonCsvReader()
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception:
            self._process = None
            return False

        self._thread = threading.Thread(target=self._reader_loop, daemon=True,
                                        name="SysmonPresentMonReader")
        self._thread.start()
        self._retry_at = 0.0
        self._launched_at = time.monotonic()
        return True

    def _reader_loop(self) -> None:
        proc = self._process
        stream = proc.stdout if proc is not None else None
        if stream is None:
            return
        try:
            while not self._stop.is_set():
                line = stream.readline()
                if not line:
                    break
                frame = self.reader.feed(line)
                if frame is None:
                    continue
                self.tracker.add_frame(frame.app, frame.pid, frame.dropped,
                                       frame.frametime_ms, frame.timestamp)
        except Exception:
            pass

    def _dispose_process(self) -> None:
        proc = self._process
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except Exception:
                pass
        self._process = None

        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._thread = None

    def stop(self) -> None:
        self._started = False
        self._stop.set()
        self._dispose_process()
        self.tracker.reset()

        # Release the ETW session so a crash cannot leave a live trace behind.
        if self._presentmon_exe and is_admin():
            try:
                subprocess.run(
                    [self._presentmon_exe,
                     "--session_name", self.SESSION,
                     "--terminate_existing_session"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=_CREATE_NO_WINDOW,
                    timeout=3.0,
                )
            except Exception:
                pass

    # ----------------------------------------------------------------- health

    def _running(self) -> bool:
        proc = self._process
        return proc is not None and proc.poll() is None

    def _ensure_running(self) -> None:
        """Bring PresentMon back if it died, with a bounded retry backoff.

        PresentMon exits immediately with status 1 and prints nothing when it
        cannot open its ETW session - unelevated, blocked by policy, or another
        instance holding the session name.  Retrying forever would burn CPU, so
        an immediate exit is remembered and the reason is surfaced instead.
        """
        if not self._started or not self._presentmon_exe or not is_admin():
            return
        if self._running():
            return

        now = time.monotonic()
        if now < self._retry_at:
            return

        if self._launched_at and now - self._launched_at < _FAST_FAILURE_SECONDS:
            self._fast_failures += 1
        self._failures = min(self._failures + 1, 4)
        self._retry_at = now + min(15.0, 1.0 * (2 ** self._failures))
        if self._failures >= 6:
            # It never once opened the session.  Stopping the loop keeps a
            # permanently broken install from spawning a process every 15s.
            self._started = False
            return
        self._launch_presentmon()

    # ----------------------------------------------------------------- sample

    def sample(self) -> FpsSnapshot:
        if self._started:
            self._ensure_running()

        if self._running():
            if self.reader.rows:
                self._failures = 0
            fg_pid, fg_exe, _title = get_foreground_process()
            fps, frametime, low, app, pid, active = self.tracker.compute_stats(target_pid=fg_pid)
            if app is None:
                app = fg_exe
            snap = FpsSnapshot(
                fps=fps,
                frametime_ms=frametime,
                fps_1percent_low=low,
                app_name=app if active else None,
                pid=pid if active else None,
                is_active=active,
                is_available=True,
                detail=self._detail(active, app),
            )
            self._last_snapshot = snap
            write_ipc_fps(snap)
            return snap

        # An elevated sibling may still be feeding the IPC file.
        ipc_snap = read_ipc_fps()
        if ipc_snap is not None:
            self._last_snapshot = ipc_snap
            return ipc_snap

        fg_pid, fg_exe, _title = get_foreground_process()
        foreground_is_game = fg_exe is not None and not _is_ignored(fg_exe)
        if not self._presentmon_exe:
            detail = "PresentMon_x64.exe not found - place it in the sysmon/sensors folder"
        elif not is_admin():
            detail = "In-game FPS requires Administrator permission (PresentMon needs it)"
        elif self._fast_failures >= 2:
            detail = ("PresentMon cannot open its ETW session - an elevated instance may "
                      "already be using it, or ETW is blocked on this machine")
        else:
            detail = "Restarting PresentMon..."
        snap = FpsSnapshot(
            fps=None,
            frametime_ms=None,
            fps_1percent_low=None,
            app_name=fg_exe if foreground_is_game else None,
            pid=fg_pid if foreground_is_game else None,
            is_active=False,
            is_available=False,
            detail=detail,
        )
        self._last_snapshot = snap
        return snap

    def _detail(self, active: bool, app: Optional[str]) -> str:
        if active and app:
            return f"PresentMon ETW active - {app}"
        if self.reader.rows:
            return "PresentMon ETW running - no frames from a 3D application yet"
        return "PresentMon ETW running - waiting for frames"

    # ----------------------------------------------------------- capabilities

    def capabilities(self) -> Dict[Capability, CapabilityState]:
        exe = self._presentmon_exe or find_presentmon()
        if not exe:
            return {Capability.FPS: unavailable(
                "PresentMon_x64.exe not found - place it in the sysmon/sensors folder")}
        if not is_admin() and read_ipc_fps() is None:
            return {Capability.FPS: unavailable(
                "Requires Administrator privileges (PresentMon cannot open its ETW session without them)")}
        return {Capability.FPS: OK}