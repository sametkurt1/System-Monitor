#!/usr/bin/env python
"""Launch sysmon desktop GUI:  python sysmon.py"""

import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# If launched on Windows under python.exe / py.exe (e.g. double-clicked in Explorer),
# re-spawn detached under pythonw.exe so the terminal / py.exe console window closes immediately!
if sys.platform == "win32" and not (len(sys.argv) >= 3 and sys.argv[1] == "--thermal-worker"):
    if "--no-relaunch" in sys.argv:
        sys.argv.remove("--no-relaunch")
    elif not sys.executable.lower().endswith("pythonw.exe"):
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = "pythonw.exe"
        try:
            import subprocess
            DETACHED_PROCESS = 0x00000008
            CREATE_NO_WINDOW = 0x08000000
            subprocess.Popen(
                [pythonw, os.path.abspath(__file__)] + sys.argv[1:],
                creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
                close_fds=True,
            )
            sys.exit(0)
        except Exception:
            pass

    # Fallback: hide console if already running in one
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--thermal-worker":
        from sysmon.sensors.thermal import run_thermal_worker
        run_thermal_worker(int(sys.argv[2]))
        sys.exit(0)

    from sysmon.gui import available
    if not available():
        sys.stderr.write(
            "The desktop GUI needs PySide6, which is not installed.\n\n"
            "    pip install -r requirements.txt\n\n"
        )
        sys.exit(2)
    from sysmon.gui.app import main
    sys.exit(main(sys.argv[1:]))
