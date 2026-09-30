"""Entry point for the sysmon desktop window."""

from __future__ import annotations

import os
import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except Exception as exc:  # pragma: no cover - depends on the environment
        sys.stderr.write(
            "The desktop GUI needs PySide6, which is not installed.\n\n"
            "    pip install -r requirements.txt\n\n"
            f"(import failed: {exc})\n"
            "The terminal UI needs no dependencies: python sysmon.py\n"
        )
        return 2

    # High-DPI: Qt 6 enables this by default, but set the rounding policy so the
    # window is crisp on mixed-DPI multi-monitor setups.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    try:
        QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass

    from . import theme as T

    app = QtWidgets.QApplication(argv)
    app.setApplicationName("sysmon")
    app.setOrganizationName("sysmon")
    T.refresh_fonts()
    app.setStyleSheet(T.STYLESHEET)
    # A dark palette stops Windows painting light chrome behind the frameless bits.
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, T.qcolor(T.BG))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, T.qcolor(T.FG))
    palette.setColor(QtGui.QPalette.ColorRole.Base, T.qcolor(T.INPUT))
    palette.setColor(QtGui.QPalette.ColorRole.Text, T.qcolor(T.FG))
    palette.setColor(QtGui.QPalette.ColorRole.Button, T.qcolor(T.CARD))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, T.qcolor(T.FG))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, T.qcolor(T.ACCENT))
    app.setPalette(palette)

    from .main_window import MainWindow

    window = MainWindow(interval=_option(argv, "--gui-interval", 1.0),
                        enable_cpu=not _flag(argv, "--gpu-only"),
                        enable_memory=not _flag(argv, "--gpu-only"),
                        enable_gpu=not _flag(argv, "--cpu-only"),
                        enable_thermal=not _flag(argv, "--no-optional"),
                        show_cores=not _flag(argv, "--no-cores"))
    window.show()
    return app.exec()


def _flag(argv: List[str], name: str) -> bool:
    return name in argv


def _option(argv: List[str], name: str, default: float) -> float:
    if name in argv:
        try:
            return float(argv[argv.index(name) + 1])
        except (IndexError, ValueError):
            return default
    return default
