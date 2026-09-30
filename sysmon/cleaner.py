"""Windows RAM Cache & Standby List cleaner.

Frees standby list pages and system file cache left behind by closed applications
without evicting active working sets of running processes.
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from typing import Optional

from .formatting import human_gb

# SYSTEM_MEMORY_LIST_COMMAND constants
MEMORY_EMPTY_WORKING_SETS = 0          # Flushes working sets of all processes (deliberately avoided)
MEMORY_FLUSH_MODIFIED_LIST = 1         # Flushes modified pages to disk so they transition to standby
MEMORY_PURGE_STANDBY_LIST = 2          # Purges pages from all standby lists to the free list
MEMORY_PURGE_LOW_PRIORITY_STANDBY = 3  # Purges pages from standby priority 0 list
SYSTEM_MEMORY_LIST_INFORMATION = 80    # SystemMemoryListInformation class (0x50)

STATUS_SUCCESS = 0
STATUS_PRIVILEGE_NOT_HELD = 0xC0000061


@dataclass(frozen=True)
class CleanResult:
    """Result of a RAM cleaning operation."""

    success: bool
    freed_bytes: int
    message: str
    needs_elevation: bool = False


def is_admin() -> bool:
    """Return True if running with Administrator privileges on Windows."""
    if sys.platform != "win32":
        return False
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def get_available_memory_bytes() -> Optional[int]:
    """Read the current physical available memory in bytes."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes.wintypes as w

        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", w.DWORD),
                ("dwMemoryLoad", w.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        ms = _MEMORYSTATUSEX()
        ms.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if k32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            return int(ms.ullAvailPhys)
    except Exception:
        pass
    return None


def get_cleanable_cache_bytes() -> Optional[int]:
    """Return the estimated number of bytes in standby and system file cache that can be purged."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import ctypes.wintypes as w

        class SYSTEM_MEMORY_LIST_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("ZeroPageCount", ctypes.c_size_t),
                ("FreePageCount", ctypes.c_size_t),
                ("ModifiedPageCount", ctypes.c_size_t),
                ("ModifiedNoWritePageCount", ctypes.c_size_t),
                ("BadPageCount", ctypes.c_size_t),
                ("PageCountByPriority", ctypes.c_size_t * 8),
                ("RepurposedPagesByPriority", ctypes.c_size_t * 8),
                ("ModifiedPageCountPageFile", ctypes.c_size_t),
            ]

        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        mem_info = SYSTEM_MEMORY_LIST_INFORMATION()
        ret_len = w.ULONG()

        status = ntdll.NtQuerySystemInformation(
            80,  # SystemMemoryListInformation
            ctypes.byref(mem_info),
            ctypes.sizeof(mem_info),
            ctypes.byref(ret_len),
        )

        if status == 0:
            standby_pages = sum(mem_info.PageCountByPriority)
            return int(standby_pages * 4096)
    except Exception:
        pass

    try:
        import ctypes
        import ctypes.wintypes as w

        class _PI(ctypes.Structure):
            _fields_ = [
                ("cb", w.DWORD),
                ("CommitTotal", ctypes.c_size_t),
                ("CommitLimit", ctypes.c_size_t),
                ("CommitPeak", ctypes.c_size_t),
                ("PhysicalTotal", ctypes.c_size_t),
                ("PhysicalAvailable", ctypes.c_size_t),
                ("SystemCache", ctypes.c_size_t),
                ("KernelTotal", ctypes.c_size_t),
                ("KernelPaged", ctypes.c_size_t),
                ("KernelNonpaged", ctypes.c_size_t),
                ("PageSize", ctypes.c_size_t),
                ("HandleCount", w.DWORD),
                ("ProcessCount", w.DWORD),
                ("ThreadCount", w.DWORD),
            ]

        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        pi = _PI()
        pi.cb = ctypes.sizeof(_PI)
        if psapi.GetPerformanceInfo(ctypes.byref(pi), ctypes.sizeof(_PI)):
            page = int(pi.PageSize) or 4096
            return int(pi.SystemCache) * page
    except Exception:
        pass

    return None


def enable_privilege(priv_name: str) -> bool:
    """Enable a specific token privilege for the current process."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes.wintypes as w

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        TOKEN_ADJUST_PRIVILEGES = 0x0020
        TOKEN_QUERY = 0x0008
        SE_PRIVILEGE_ENABLED = 0x00000002

        class LUID(ctypes.Structure):
            _fields_ = [("LowPart", w.DWORD), ("HighPart", ctypes.c_long)]

        class LUID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Luid", LUID), ("Attributes", w.DWORD)]

        class TOKEN_PRIVILEGES(ctypes.Structure):
            _fields_ = [
                ("PrivilegeCount", w.DWORD),
                ("Privileges", LUID_AND_ATTRIBUTES * 1),
            ]

        h_token = w.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
            ctypes.byref(h_token),
        ):
            return False

        try:
            luid = LUID()
            if not advapi32.LookupPrivilegeValueW(None, priv_name, ctypes.byref(luid)):
                return False

            tp = TOKEN_PRIVILEGES()
            tp.PrivilegeCount = 1
            tp.Privileges[0].Luid = luid
            tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED

            advapi32.AdjustTokenPrivileges(
                h_token, False, ctypes.byref(tp), ctypes.sizeof(tp), None, None
            )
            err = kernel32.GetLastError()
            return err in (0, 1300)
        finally:
            kernel32.CloseHandle(h_token)
    except Exception:
        return False


def clean_ram() -> CleanResult:
    """Purge Windows standby list and system file cache.

    Targeted memory areas:
      1. Standby List: pages cached from closed applications and file reads
      2. Modified List: flushed to disk so dirty pages transition to standby
      3. System File Cache working set: flushed via SetSystemFileCacheSize

    Deliberately does NOT touch active processes' working sets (command 0),
    so running programs are NOT trimmed into pagefiles and experience no stutter.
    """
    if sys.platform != "win32":
        return CleanResult(False, 0, "RAM cache cleaning is Windows-only.")

    # Request required privileges
    p_profile = enable_privilege("SeProfileSingleProcessPrivilege")
    enable_privilege("SeIncreaseQuotaPrivilege")

    if not is_admin() and not p_profile:
        return CleanResult(
            False,
            0,
            "Administrator privileges required to purge the standby list.",
            needs_elevation=True,
        )

    before_cache = get_cleanable_cache_bytes()
    before_avail = get_available_memory_bytes() or 0

    try:
        import ctypes.wintypes as w

        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        ntdll.NtSetSystemInformation.argtypes = [w.DWORD, ctypes.c_void_p, w.ULONG]
        ntdll.NtSetSystemInformation.restype = ctypes.c_long

        # 1. Flush system file cache working set (freed from closed files)
        try:
            k32.SetSystemFileCacheSize.argtypes = [ctypes.c_size_t, ctypes.c_size_t, w.DWORD]
            k32.SetSystemFileCacheSize.restype = w.BOOL
            k32.SetSystemFileCacheSize(ctypes.c_size_t(-1), ctypes.c_size_t(-1), 0)
        except Exception:
            pass

        # 2. Flush modified page list so pages transition to standby
        cmd_flush = ctypes.c_int(MEMORY_FLUSH_MODIFIED_LIST)
        ntdll.NtSetSystemInformation(
            SYSTEM_MEMORY_LIST_INFORMATION,
            ctypes.byref(cmd_flush),
            ctypes.sizeof(cmd_flush),
        )

        # 3. Purge all standby lists (moves cached pages of closed apps to free list)
        cmd_purge = ctypes.c_int(MEMORY_PURGE_STANDBY_LIST)
        status = ntdll.NtSetSystemInformation(
            SYSTEM_MEMORY_LIST_INFORMATION,
            ctypes.byref(cmd_purge),
            ctypes.sizeof(cmd_purge),
        )

        if status != STATUS_SUCCESS and (status & 0xFFFFFFFF) == STATUS_PRIVILEGE_NOT_HELD:
            return CleanResult(
                False,
                0,
                "SeProfileSingleProcessPrivilege not held. Please run as Administrator.",
                needs_elevation=True,
            )
        elif status != STATUS_SUCCESS:
            return CleanResult(
                False,
                0,
                f"NtSetSystemInformation failed with code 0x{status & 0xFFFFFFFF:08X}.",
            )

        after_cache = get_cleanable_cache_bytes()
        after_avail = get_available_memory_bytes() or before_avail

        if before_cache is not None and after_cache is not None:
            freed = max(0, before_cache - after_cache)
        else:
            freed = max(0, after_avail - before_avail)

        if freed > 0:
            msg = f"RAM cache cleaned ({human_gb(freed)} freed)"
        else:
            msg = "RAM standby cache was already clean"

        return CleanResult(True, freed, msg)
    except Exception as exc:
        return CleanResult(False, 0, f"Cleanup error: {exc}")


def clean_ram_with_elevation() -> CleanResult:
    """Run clean_ram; if elevation is needed, invoke a quick elevated helper via UAC."""
    res = clean_ram()
    if res.success or not res.needs_elevation:
        return res

    if sys.platform != "win32":
        return res

    try:
        import ctypes.wintypes as w

        class SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize", w.DWORD),
                ("fMask", w.ULONG),
                ("hwnd", w.HWND),
                ("lpVerb", w.LPCWSTR),
                ("lpFile", w.LPCWSTR),
                ("lpParameters", w.LPCWSTR),
                ("lpDirectory", w.LPCWSTR),
                ("nShow", ctypes.c_int),
                ("hInstApp", w.HINSTANCE),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", w.LPCWSTR),
                ("hkeyClass", w.HKEY),
                ("dwHotKey", w.DWORD),
                ("hIconOrMonitor", w.HANDLE),
                ("hProcess", w.HANDLE),
            ]

        SEE_MASK_NOCLOSEPROCESS = 0x00000040
        SW_HIDE = 0

        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if getattr(sys, "frozen", False):
            lp_file = sys.executable
            lp_params = "--clean-ram"
        else:
            lp_file = sys.executable
            code = (
                f"import sys; sys.path.insert(0, {repr(repo_dir)});"
                "from sysmon.cleaner import clean_ram; sys.exit(0 if clean_ram().success else 1)"
            )
            lp_params = f'-c "{code}"'

        before_cache = get_cleanable_cache_bytes()
        before_avail = get_available_memory_bytes() or 0

        sei = SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS
        sei.hwnd = None
        sei.lpVerb = "runas"
        sei.lpFile = lp_file
        sei.lpParameters = lp_params
        sei.lpDirectory = repo_dir
        sei.nShow = SW_HIDE

        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        if not shell32.ShellExecuteExW(ctypes.byref(sei)):
            err = kernel32.GetLastError()
            if err == 1223:  # ERROR_CANCELLED (user clicked No on UAC)
                return CleanResult(False, 0, "Administrator elevation cancelled by user.")
            return CleanResult(False, 0, f"UAC elevation failed (error {err}).")

        # Wait for the helper process to finish (up to 8 seconds)
        success = True
        if sei.hProcess:
            kernel32.WaitForSingleObject(sei.hProcess, 8000)
            exit_code = w.DWORD()
            kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code))
            kernel32.CloseHandle(sei.hProcess)
            success = exit_code.value == 0

        after_cache = get_cleanable_cache_bytes()
        after_avail = get_available_memory_bytes() or before_avail

        if before_cache is not None and after_cache is not None:
            freed = max(0, before_cache - after_cache)
        else:
            freed = max(0, after_avail - before_avail)

        if success:
            msg = f"RAM cache cleaned ({human_gb(freed)} freed)" if freed > 0 else "RAM standby cache cleared"
            return CleanResult(True, freed, msg)
        else:
            return CleanResult(False, 0, "Elevated cleaner process failed.")
    except Exception as exc:
        return CleanResult(False, 0, f"Elevation error: {exc}")
