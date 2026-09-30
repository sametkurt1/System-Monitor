#!/usr/bin/env pythonw
"""Launch sysmon desktop GUI without a console window: pythonw sysmon.pyw"""

import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
