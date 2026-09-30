"""Desktop GUI for sysmon.

A PySide6 front-end over the same zero-dependency sensor layer the terminal UI uses,
so every reading (and every ``N/A``) comes from exactly the same place.

PySide6 is an *optional* dependency.  Submodules are imported lazily so that, for
example, ``sysmon.gui.theme`` can be used without pulling in the whole app.
"""

from typing import Any

__all__ = ["main", "available", "missing_reason"]

_INSTALL_HINT = (
    "The desktop GUI needs PySide6.\n\n"
    "    pip install -r requirements.txt\n\n"
)


def available() -> bool:
    try:
        import importlib

        importlib.import_module("PySide6")
        return True
    except Exception:
        return False


def missing_reason() -> str:
    return _INSTALL_HINT


def __getattr__(name: str) -> Any:
    if name in ("main", "run"):
        from .app import main
        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
