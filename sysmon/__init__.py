"""sysmon - a btop-style CPU / RAM / NVIDIA GPU monitor for the Windows terminal.

Pure-Python. No third-party packages are required: CPU and RAM readings come from
native Windows APIs through ctypes, and NVIDIA readings come from NVML.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
