#!/usr/bin/env python
"""Launch sysmon desktop GUI:  python sysmon.py

Start-up order (see :mod:`sysmon.elevation`):
  1. re-launch elevated through UAC unless the process already is an admin;
  2. hand over to ``pythonw.exe`` so no console window is left behind;
  3. run the GUI.
"""

import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sysmon.elevation import prepare_startup

if __name__ == "__main__":
    argv = sys.argv[1:]

    if len(argv) >= 2 and argv[0] == "--thermal-worker":
        from sysmon.sensors.thermal import run_thermal_worker
        run_thermal_worker(int(argv[1]))
        sys.exit(0)

    argv = prepare_startup(argv)
    if argv is None:
        # A privileged copy of this process took over.
        sys.exit(0)
    sys.argv = [sys.argv[0]] + argv

    from sysmon.gui import available
    if not available():
        sys.stderr.write(
            "The desktop GUI needs PySide6, which is not installed.\n\n"
            "    pip install -r requirements.txt\n\n"
        )
        sys.exit(2)
    from sysmon.gui.app import main
    sys.exit(main(sys.argv[1:]))