"""Command line interface."""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__
from .app import App
from .collector import Collector

_LHM_NUGET = "https://api.nuget.org/v3-flatcontainer/librehardwaremonitorlib/0.9.3/librehardwaremonitorlib.0.9.3.nupkg"

# The net472 build of LibreHardwareMonitorLib loads reliably on Windows under pythonnet
# without requiring .NET 10 or extra shims.
_LHM_FILES = (
    ("lib/net472/LibreHardwareMonitorLib.dll", "LibreHardwareMonitorLib.dll"),
)
_LHM_SHIMS = ()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sysmon",
        description="A btop-style CPU / RAM / NVIDIA GPU monitor for the Windows terminal.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  sysmon                     run with a 1.0s refresh\n"
            "  sysmon -i 0.5              refresh twice a second\n"
            "  sysmon --once              print one frame and exit (useful for logs/CI)\n"
            "  sysmon diagnose            explain which sensors are available and why\n"
            "  sysmon sensors install     fetch the optional CPU temperature/power provider\n"
        ),
    )
    p.add_argument("-i", "--interval", type=float, default=1.0,
                   metavar="SEC", help="refresh interval in seconds (0.1-10, default 1.0)")
    p.add_argument("-1", "--once", action="store_true",
                   help="render a single frame and exit")
    p.add_argument("--no-cores", action="store_true", help="hide the per-core grid")
    p.add_argument("--no-color", action="store_true", help="disable colour output")
    p.add_argument("--no-alt-screen", action="store_true",
                   help="do not switch to the alternate screen buffer")
    p.add_argument("--cpu-only", action="store_true", help="disable the GPU provider")
    p.add_argument("--gpu-only", action="store_true", help="disable CPU/RAM providers")
    p.add_argument("--no-optional", action="store_true",
                   help="never load the optional LibreHardwareMonitor provider")
    p.add_argument("-g", "--gui", action="store_true",
                   help="open the desktop window instead of the terminal UI "
                        "(requires PySide6: pip install -r requirements-gui.txt)")
    p.add_argument("--gui-interval", type=float, default=1.0, metavar="SEC",
                   help="refresh interval for the desktop window (0.2-10)")
    p.add_argument("--size", metavar="WxH", default=None,
                   help="force the layout size (useful when stdout is redirected)")
    p.add_argument("--exit-after", type=float, default=None, metavar="SEC",
                   help="quit automatically after SEC seconds (for smoke tests / CI)")
    p.add_argument("--clean-ram", action="store_true",
                   help="purge standby list and system file cache, then exit")
    p.add_argument("--version", action="version", version=f"sysmon {__version__}")

    sub = p.add_subparsers(dest="command")

    d = sub.add_parser("diagnose", help="report sensor availability and why anything is N/A")
    d.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    s = sub.add_parser("sensors", help="manage the optional sensor provider")
    s.add_argument("action", choices=["install", "status", "remove"])

    c = sub.add_parser("clean-ram", help="purge standby list and system file cache")
    c.add_argument("--elevate", action="store_true",
                   help="prompt for Administrator elevation (UAC) if not already admin")

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "diagnose":
        return _cmd_diagnose(args)
    if args.command == "sensors":
        return _cmd_sensors(args)
    if args.command == "clean-ram" or args.clean_ram:
        return _cmd_clean_ram(args)

    if args.gui:
        from .gui import available
        if not available():
            sys.stderr.write(
                "The desktop GUI needs PySide6, which is not installed.\n\n"
                "    pip install -r requirements-gui.txt\n\n"
                "The terminal UI needs no dependencies:  python sysmon.py\n"
            )
            return 2
        if args.interval is not None and not (0.2 <= args.interval <= 10):
            parser.error("--gui-interval must be between 0.2 and 10 seconds")
        argv = ["--gui-interval", str(args.interval if args.interval else 1.0)]
        if args.cpu_only:
            argv.append("--cpu-only")
        if args.gpu_only:
            argv.append("--gpu-only")
        if args.no_optional:
            argv.append("--no-optional")
        if args.no_cores:
            argv.append("--no-cores")
        from .gui.app import main as gui_main
        return gui_main(argv)

    if args.interval is not None and not (0.05 <= args.interval <= 60):
        parser.error("interval must be between 0.05 and 60 seconds")

    collector = Collector(
        enable_cpu=not args.gpu_only,
        enable_memory=not args.gpu_only,
        enable_gpu=not args.cpu_only,
        enable_thermal=not args.no_optional,
    )
    app = App(collector, interval=args.interval, show_cores=not args.no_cores,
              once=args.once)
    if args.no_color:
        app.force_no_color = True
    if args.no_alt_screen:
        app.use_alt_screen = False
    if args.size:
        try:
            w, _, h = args.size.lower().partition("x")
            app.forced_size = (int(w), int(h))
        except ValueError:
            parser.error("--size expects WxH, e.g. --size 120x40")
    app.exit_after = args.exit_after
    return app.run()


def _cmd_diagnose(args) -> int:
    from .diagnose import report
    collector = Collector(enable_thermal=True)
    collector.start()
    try:
        if args.json:
            import json
            payload = {}
            for prov in (collector.cpu, collector.memory, collector.gpu, collector.thermal):
                if prov is None:
                    continue
                try:
                    caps = prov.capabilities()
                except Exception as exc:
                    payload[prov.name] = {"error": str(exc)}
                    continue
                payload[prov.name] = {
                    c.value: {"status": s.status.value, "detail": s.detail}
                    for c, s in caps.items()
                }
            print(json.dumps(payload, indent=2))
            return 0
        snap = None
        for _ in range(3):
            collector.warm(1)
            snap = collector.sample()
            if snap is not None and snap.cpu.usage is not None:
                break
        for line in report(collector):
            print(line)
        if snap is not None:
            print(" live sample")
            print(f"   CPU    {snap.cpu.name}  usage="
                  f"{'N/A' if snap.cpu.usage is None else f'{snap.cpu.usage:.0f}%'}")
            print(f"   Memory {'N/A' if snap.memory.total_bytes is None else snap.memory.total_bytes / 2**30:.1f} GiB  "
                  f"usage={'N/A' if snap.memory.usage is None else f'{snap.memory.usage:.0f}%'}")
            if snap.gpus:
                for g in snap.gpus:
                    print(f"   GPU{g.index}  {g.name}  usage="
                          f"{'N/A' if g.usage is None else f'{g.usage:.0f}%'}  "
                          f"temp={'N/A' if g.temperature_c is None else f'{g.temperature_c:.0f}C'}  "
                          f"power={'N/A' if g.power_w is None else f'{g.power_w:.0f}W'}")
            else:
                print("   GPU    none detected")
            print()
        return 0
    finally:
        collector.stop()


def _cmd_sensors(args) -> int:
    from .sensors.thermal import find_assembly

    existing = find_assembly()
    if existing:
        sensors_dir = os.path.dirname(existing)
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        sensors_dir = os.path.join(here, "sensors")

    if args.action == "status":
        found = find_assembly()
        if found:
            print(f"LibreHardwareMonitor assembly: {found}")
            print("Provider will load automatically. Run elevated for CPU temp/power.")
        else:
            print("LibreHardwareMonitor assembly: not installed")
            print("Run `sysmon sensors install` to enable CPU temperature and power.")
        return 0 if found else 1

    if args.action == "remove":
        found = find_assembly()
        removed = 0
        targets = [found] if found else []
        for extra in ("System.Memory.dll",
                      "System.Runtime.CompilerServices.Unsafe.dll"):
            candidate = os.path.join(sensors_dir, extra)
            if os.path.isfile(candidate):
                targets.append(candidate)
        if not targets:
            print("Nothing to remove.")
            return 0
        for path in targets:
            try:
                os.remove(path)
                print(f"  removed {path}")
                removed += 1
            except OSError as exc:
                print(f"  could not remove {path}: {exc}", file=sys.stderr)
        return 0 if removed == len(targets) else 1

    # install
    print(f"Installing optional sensor provider into {sensors_dir}")
    print()
    print("  NOTE: LibreHardwareMonitorLib ships a WinRing0-derived kernel driver that")
    print("        Microsoft Defender flags and that is on the Windows 11 vulnerable-")
    print("        driver blocklist.  It also needs an elevated shell to read sensors.")
    print("        Without it these two fields just report N/A, which is also correct.")
    print()
    try:
        import io
        import urllib.request
        import zipfile
    except Exception as exc:  # pragma: no cover
        print(f"  cannot prepare download: {exc}", file=sys.stderr)
        return 1

    def fetch(url: str) -> bytes:
        with urllib.request.urlopen(url, timeout=90) as resp:
            return resp.read()

    def extract(payload: bytes, member_suffix: str, dest: str) -> None:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            match = next((n for n in zf.namelist() if n.endswith(member_suffix)), None)
            if match is None:
                raise FileNotFoundError(f"{member_suffix} not in package")
            with open(dest, "wb") as fh:
                fh.write(zf.read(match))
        print(f"  wrote {dest}")

    os.makedirs(sensors_dir, exist_ok=True)
    try:
        print("  downloading LibreHardwareMonitor 0.9.3 (net472) ...")
        payload = fetch(_LHM_NUGET)
        extract(payload, "lib/net472/LibreHardwareMonitorLib.dll",
                os.path.join(sensors_dir, "LibreHardwareMonitorLib.dll"))
    except Exception as exc:
        print(f"  install failed: {exc}", file=sys.stderr)
        return 1

    for url, member, name in _LHM_SHIMS:
        try:
            extract(fetch(url), member, os.path.join(sensors_dir, name))
        except Exception as exc:
            # Non-fatal: the provider reports the precise missing-assembly error.
            print(f"  warning: could not fetch {name} ({exc})", file=sys.stderr)

    print()
    print("Next steps:")
    print("  1. pip install pythonnet        # the CLR host")
    print("  2. run sysmon from an elevated shell (Run as Administrator for CPU temperature)")
    print("  3. sysmon diagnose             # confirm the sensors appear")
    return 0


def _cmd_clean_ram(args) -> int:
    from .cleaner import clean_ram, clean_ram_with_elevation, get_cleanable_cache_bytes
    from .formatting import human_gb

    est = get_cleanable_cache_bytes()
    if est is not None and est > 0:
        print(f"sysmon: Estimated cleanable standby cache: {human_gb(est)}")

    elevate = getattr(args, "elevate", False)
    res = clean_ram_with_elevation() if elevate else clean_ram()
    if res.success:
        print(f"sysmon: {res.message}")
        return 0
    else:
        sys.stderr.write(f"sysmon: {res.message}\n")
        if res.needs_elevation and not elevate:
            sys.stderr.write("Tip: Run as Administrator or use 'sysmon clean-ram --elevate'\n")
        return 1

