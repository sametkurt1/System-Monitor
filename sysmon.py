#!/usr/bin/env python
"""Launch sysmon desktop GUI:  python sysmon.py"""

import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Hide console window immediately on Windows if launched standalone (e.g. double-clicked)
if sys.platform == "win32" and not (len(sys.argv) >= 3 and sys.argv[1] == "--thermal-worker"):
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            pids = (ctypes.c_uint * 2)()
            count = ctypes.windll.kernel32.GetConsoleProcessList(pids, 2)
            if count <= 2:
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
