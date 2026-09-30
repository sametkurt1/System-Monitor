"""Self-contained checks for sysmon.  Run with:  python -m tests.run_tests
or simply:  python tests/run_tests.py

The tests that touch hardware are skipped automatically when the corresponding
sensor is not present, so this is safe to run anywhere on Windows.
"""

from __future__ import annotations

import ctypes
import io
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sysmon import formatting
from sysmon.collector import Collector
from sysmon.console import Console
from sysmon.layout import Layout
from sysmon.models import CpuCore, CpuSnapshot, GpuSnapshot, MemorySnapshot, Snapshot
from sysmon.theme import Style
from sysmon.widgets import Glyphs, frame, gauge, grid, pad


class Recorder(Console):
    """A Console that records frames instead of painting them."""

    def __init__(self, size=(100, 30)):
        super().__init__(stream=io.StringIO())
        self.vt_ok = True
        self.unicode_ok = True
        self.size_value = size
        self.frames: list[list[str]] = []
        self.keys: list[str] = []

    def setup(self, use_alt_screen=True): pass
    def teardown(self): pass
    def size(self): return self.size_value
    def invalidate(self):
        self._need_full_repaint = True
        self._prev_lines = []
    def render(self, lines): self.frames.append(list(lines))
    def poll_key(self): return self.keys.pop(0) if self.keys else None

    def last(self):
        return self.frames[-1] if self.frames else []


# --------------------------------------------------------------- unit tests

def test_text_width_ignores_ansi():
    from sysmon.theme import Palette
    s = Style(color=True).paint("hello", (1, 2, 3))
    assert formatting.text_width(s) == 5, formatting.text_width(s)
    assert Palette.RESET in s
    assert formatting.text_width("") == 0
    assert formatting.text_width("ab") == 2


def test_text_width_counts_wide_chars():
    # A CJK ideograph occupies two cells.
    assert formatting.text_width("\u4e2d\u6587") == 4
    assert formatting.text_width("a\u4e2d") == 3


def test_pad_never_exceeds_width():
    for width in range(0, 20):
        for text in ("", "a", "abcdefghijklmnopqrs", "\u4e2d\u6587\u6587\u6587"):
            out = pad(text, width)
            assert formatting.text_width(out) <= width, (text, width, out)


def test_pad_truncates_with_ellipsis():
    out = pad("abcdefghij", 5)
    assert formatting.text_width(out) == 5
    assert out[-1] in ("\u2026", "~"), out


def test_ascii_mode_switches_glyphs():
    """formatting helpers must follow the terminal's code page."""
    try:
        formatting.set_ascii_mode(True)
        assert formatting.ELLIPSIS == "~"
        assert formatting.human_temp(58.4) == "58 C"
        assert pad("abcdefgh", 4).endswith("~")
        for v in (formatting.human_gb(2 ** 30), formatting.human_temp(50),
                  formatting.human_freq(1000), formatting.human_watt(12.3),
                  pad("abcdef", 3)):
            assert all(ord(c) < 128 for c in v), v
    finally:
        formatting.set_ascii_mode(False)
    assert formatting.human_temp(58.4) == "58 \u00b0C"
    assert formatting.ELLIPSIS == "\u2026"


def test_pad_alignments():
    assert pad("x", 5) == "x    "
    assert pad("x", 5, "right") == "    x"
    assert formatting.text_width(pad("x", 4, "center")) == 4


def test_pad_preserves_ansi_width():
    coloured = Style(color=True).paint("abcdef", (9, 9, 9))
    assert formatting.text_width(pad(coloured, 4)) == 4


def test_gauge_handles_full_and_empty():
    s = Style(color=False)
    g = Glyphs(True)
    for value, expect_full in ((0, False), (50, True), (100, True), (None, False)):
        out = gauge(value, 10, s, g)
        assert formatting.text_width(out) == 10, (value, out)


def test_gauge_never_index_error():
    """The eighth-block table has 8 glyphs; rounding up must not overrun it."""
    s = Style(color=False)
    g = Glyphs(True)
    for value in (0, 1, 12.4, 12.5, 49.9, 50, 87.4, 87.5, 99.9, 100, 150, -5, None):
        assert formatting.text_width(gauge(value, 10, s, g)) == 10


def test_gauge_width_zero():
    s = Style(color=False)
    assert gauge(50, 0, s, Glyphs(True)) == ""


def test_grid_is_flat_and_fits():
    s = Style(color=False)
    cells = [f"cell{i:02d}" for i in range(16)]
    rows = grid(cells, columns_=4, width=80, style=s, glyphs=Glyphs(True))
    assert len(rows) == 4
    for r in rows:
        assert formatting.text_width(r) <= 80


def test_grid_empty():
    assert grid([], columns_=4, width=80, style=Style(color=False), glyphs=Glyphs(True)) == []


def test_ascii_glyphs_have_no_unicode():
    g = Glyphs(unicode_ok=False)
    for attr in ("tl", "tr", "bl", "br", "h", "v", "lt", "rt", "tt", "bt", "x"):
        v = getattr(g, attr)
        assert all(ord(c) < 128 for c in v), (attr, v)


def test_frame_fits_and_shows_title():
    body = ["hello", "world"]
    lines = frame(body, 40, Style(color=False), Glyphs(True), title="T", subtitle="S")
    assert len(lines) == len(body) + 2
    for l in lines:
        assert formatting.text_width(l) == 40, repr(l)
    assert "T" in formatting.strip_ansi(lines[0])
    assert "S" in formatting.strip_ansi(lines[0])


def test_frame_drops_title_when_too_narrow():
    lines = frame(["x"], 20, Style(color=False), Glyphs(True),
                  title="SYSTEM MONITOR", subtitle="a-very-long-subtitle-here")
    assert formatting.text_width(lines[0]) == 20


def test_human_formatting():
    assert formatting.human_gb(16 * 1024 ** 3) == "16.0 GB"
    assert formatting.human_bytes(1536) == "1.5 KiB"
    assert formatting.human_gb(None) == "N/A"
    assert formatting.human_temp(None) == "N/A"
    assert formatting.human_temp(58.4) == "58 \u00b0C"
    assert formatting.human_freq(4650) == "4.65 GHz"
    assert formatting.human_freq(800) == "800 MHz"
    assert formatting.human_watt(None) == "N/A"
    assert formatting.pct(None) == "N/A"
    assert formatting.pct(41.4) == "41%"


def test_strip_ansi():
    assert formatting.strip_ansi("\x1b[1;31mred\x1b[0m") == "red"
    assert formatting.strip_ansi("plain") == "plain"


# ------------------------------------------------------------ layout tests

def _snap(with_gpu=True, cores=16):
    return Snapshot(
        monotonic=0.0,
        cpu=CpuSnapshot(
            name="Test CPU 16-Core", usage=42.0, cores_physical=8, cores_logical=cores,
            base_clock_mhz=3600.0, temperature_c=58.0, power_w=72.5,
            frequency_mhz=4650.0, frequency_max_mhz=4700.0,
            per_core=tuple(CpuCore(index=i, usage=float(i * 6 % 101), frequency_mhz=4000.0)
                           for i in range(cores)),
        ),
        memory=MemorySnapshot(total_bytes=16 * 1024 ** 3, used_bytes=int(7.2 * 1024 ** 3),
                              available_bytes=int(8.8 * 1024 ** 3), usage=45.0,
                              committed_bytes=int(9 * 1024 ** 3),
                              commit_limit_bytes=int(32 * 1024 ** 3)),
        gpus=(GpuSnapshot(index=0, name="Test GPU", usage=67.0, temperature_c=64.0,
                          power_w=218.0, power_limit_w=300.0,
                          memory_used_bytes=int(8.4 * 1024 ** 3),
                          memory_total_bytes=int(16 * 1024 ** 3),
                          clock_mhz=2760.0, clock_memory_mhz=14001.0,
                          clock_max_mhz=3090.0, clock_memory_max_mhz=14001.0,
                          fan_percent=45),) if with_gpu else (),
    )


SIZES = [(200, 60), (160, 50), (120, 44), (120, 40), (100, 34), (100, 30),
         (92, 28), (80, 24), (76, 22), (75, 22), (60, 20), (52, 16),
         (45, 12), (40, 10), (40, 6), (40, 5)]


def test_layout_fits_every_size():
    snap = _snap()
    bad = []
    for w, h in SIZES:
        for color in (False, True):
            lay = Layout(Style(color=color), Glyphs(True))
            body = lay.build(snap, max(20, w - 2), h, "", False)
            lines = frame(body, w, Style(color=color), Glyphs(True),
                          title="SYSTEM MONITOR", subtitle=f"{w}x{h}")
            for i, l in enumerate(lines):
                if formatting.text_width(l) > w:
                    bad.append((w, h, color, i, formatting.text_width(l)))
            if len(lines) > h:
                bad.append((w, h, color, "height", len(lines)))
    assert not bad, bad[:6]


def test_layout_without_gpu():
    snap = _snap(with_gpu=False)
    lay = Layout(Style(color=False), Glyphs(True))
    body = lay.build(snap, 98, 30, "", False)
    text = "\n".join(formatting.strip_ansi(x) for x in body).lower()
    assert "no nvidia gpu detected" in text, text[:400]


def test_layout_all_missing():
    snap = Snapshot()
    lay = Layout(Style(color=False), Glyphs(True))
    body = lay.build(snap, 98, 30, "", False)
    text = "\n".join(formatting.strip_ansi(x) for x in body)
    assert "N/A" in text
    for l in body:
        assert formatting.text_width(l) <= 98


def test_layout_ascii_glyphs():
    snap = _snap()
    try:
        lay = Layout(Style(color=False), Glyphs(unicode_ok=False))
        body = lay.build(snap, 78, 30, "", False)
        for l in body:
            assert all(ord(c) < 128 for c in formatting.strip_ansi(l)), repr(l)
        lines = frame(body, 78, Style(color=False), Glyphs(unicode_ok=False),
                      title="SYSTEM MONITOR", subtitle="78x30")
        for l in lines:
            assert all(ord(c) < 128 for c in formatting.strip_ansi(l)), repr(l)
    finally:
        formatting.set_ascii_mode(False)


def test_layout_no_color_is_plain():
    snap = _snap()
    lay = Layout(Style(color=False), Glyphs(True))
    body = lay.build(snap, 98, 30, "", False)
    for l in body:
        assert "\x1b" not in l


# ----------------------------------------------------------- app behaviour

def test_app_quits_and_restores():
    from sysmon.app import App
    con = Recorder((100, 30))
    app = App(Collector(), interval=0.1)
    app.console = con
    app._build_ui()
    app._snapshot = Snapshot()
    app._paint()
    con.keys = ["q"]
    app._handle_keys()
    assert app._running is False


def test_app_ignores_unknown_keys():
    from sysmon.app import App
    con = Recorder((100, 30))
    app = App(Collector(), interval=1.0)
    app.console = con
    app._build_ui()
    app._snapshot = Snapshot()
    con.keys = ["z", "9", "!", "ctrl-a", "f5"]
    app._handle_keys()
    assert app._running is True
    assert abs(app.interval - 1.0) < 1e-9


def test_app_interval_clamped():
    from sysmon.app import App
    con = Recorder((100, 30))
    app = App(Collector(), interval=1.0)
    app.console = con
    app._build_ui()
    app._snapshot = Snapshot()
    con.keys = ["-"] * 200
    app._handle_keys()
    assert app.interval >= 0.10
    con.keys = ["+"] * 500
    app._handle_keys()
    assert app.interval <= 10.0


def test_app_pause_and_cores_toggle():
    from sysmon.app import App
    con = Recorder((100, 30))
    app = App(Collector(), interval=1.0)
    app.console = con
    app._build_ui()
    app._snapshot = _snap()
    con.keys = ["p", "c"]
    app._handle_keys()
    assert app._paused is True
    assert app._show_cores is False
    con.keys = ["p", "c"]
    app._handle_keys()
    assert app._paused is False
    assert app._show_cores is True


def test_app_footer_fits():
    from sysmon.app import App
    con = Recorder((100, 30))
    app = App(Collector(), interval=1.0)
    app.console = con
    app._build_ui()
    for w in (40, 50, 60, 80, 100, 200):
        f = app._footer(w)
        assert formatting.text_width(f) == w, (w, formatting.text_width(f))


def test_app_frame_fits_after_resize():
    from sysmon.app import App
    con = Recorder((100, 30))
    app = App(Collector(), interval=0.1)
    app.console = con
    app._build_ui()
    app._snapshot = _snap()
    problems = []
    for w, h in SIZES:
        con.size_value = (w, h)
        con.invalidate()
        try:
            app._paint()
        except Exception as exc:
            problems.append((w, h, repr(exc)))
            continue
        lines = con.last()
        if not lines:
            problems.append((w, h, "no frame"))
            continue
        if len(lines) > h:
            problems.append((w, h, f"{len(lines)} rows > {h}"))
        for i, l in enumerate(lines):
            if formatting.text_width(l) > w:
                problems.append((w, h, f"row {i} width {formatting.text_width(l)}"))
            if "layout error" in formatting.strip_ansi(l):
                problems.append((w, h, formatting.strip_ansi(l).strip()[:80]))
    assert not problems, problems[:6]


class PaintConsole(Console):
    """A Console with the *real* render(), writing to an inspectable buffer."""

    def __init__(self, size=(40, 10), vt=True):
        self.buffer = io.StringIO()
        super().__init__(stream=self.buffer)
        self.vt_ok = vt
        self.unicode_ok = True
        self.size_value = size

    def setup(self, use_alt_screen=True): pass
    def teardown(self): pass
    def size(self): return self.size_value
    def poll_key(self): return None

    def take(self):
        data = self.buffer.getvalue()
        self.buffer.seek(0)
        self.buffer.truncate(0)
        return data


def test_renderer_only_rewrites_changed_lines():
    """The whole point of the diff renderer: no redundant repaints."""
    con = PaintConsole()
    con.render(["a", "b", "c"])
    first = con.take()
    assert first.count("\x1b[2J") == 1, "first frame should clear once"

    con.render(["a", "b", "c"])
    assert con.take() == "", "identical frame must emit nothing"

    con.render(["a", "B", "c"])
    out = con.take()
    assert "\x1b[2J" not in out, "a one-line change must not clear the screen"
    # Exactly one repaint: address row 2, erase to end of line, then write "B".
    assert out == "\x1b[2;1H\x1b[KB\x1b[3;1H", repr(out)
    assert "\x1b[1;1H" not in out, "unchanged line 1 must not be addressed"


def test_renderer_repaints_all_after_resize():
    con = PaintConsole()
    con.render(["a", "b", "c"])
    con.take()
    con.invalidate()
    con.render(["a", "b", "c"])
    out = con.take()
    assert "\x1b[2J" in out, "invalidate() should force a full repaint"
    for row in (1, 2, 3):
        assert f"\x1b[{row};1H" in out


def test_renderer_erases_surplus_lines_when_shrinking():
    con = PaintConsole()
    con.render(["a", "b", "c", "d", "e"])
    con.take()
    con.render(["a", "b"])
    out = con.take()
    for row in (3, 4, 5):
        assert f"\x1b[{row};1H" in out, f"row {row} should be erased"


def test_renderer_ascii_fallback_does_not_emit_escapes():
    con = PaintConsole(vt=False)
    con.render(["a", "b"])
    out = con.take()
    assert "\x1b[" not in out, out
    assert out.strip() == "a\nb"


def test_layout_omits_disabled_categories():
    """A category whose provider is off must not be drawn as a column of N/A."""
    snap = _snap()
    lay = Layout(Style(color=False), Glyphs(True), enabled={"GPU"})
    body = lay.build(snap, 98, 30, "", False)
    text = "\n".join(formatting.strip_ansi(x) for x in body)
    assert "GPU" in text
    assert "CPU CORES" not in text
    assert "Ryzen" not in text, "CPU panel should be omitted entirely"
    assert "MEMORY" not in text
    for l in body:
        assert formatting.text_width(l) <= 98


def test_layout_enabled_subset_stack():
    snap = _snap()
    for enabled in ({"CPU"}, {"MEMORY"}, {"CPU", "MEMORY"}, {"CPU", "GPU"}, set()):
        lay = Layout(Style(color=False), Glyphs(True), enabled=enabled)
        body = lay.build(snap, 78, 26, "", False)
        for l in body:
            assert formatting.text_width(l) <= 78, (enabled, repr(l))


def test_collector_reports_enabled_categories():
    from sysmon.collector import Collector
    assert Collector().enabled_categories() == {"CPU", "MEMORY", "GPU"}
    assert Collector(enable_cpu=False).enabled_categories() == {"MEMORY", "GPU"}
    assert Collector(enable_gpu=False, enable_memory=False).enabled_categories() == {"CPU"}
    assert Collector(enable_cpu=False, enable_memory=False,
                     enable_gpu=False).enabled_categories() == set()
    return "enabled_categories() tracks provider attachment"


# --------------------------------------------------------- hardware tests

def test_cpu_source():
    from sysmon.sensors.cpu import CpuSource
    s = CpuSource()
    s.start()
    try:
        caps = s.capabilities()
        assert caps, "no capabilities reported"
        s.sample(); time.sleep(0.5)
        snap = s.sample()
        if snap is None:
            return "skipped: CPU counters produced no sample"
        assert snap.usage is not None, "total CPU usage unavailable"
        assert 0.0 <= snap.usage <= 100.0 + 40, snap.usage
        if snap.per_core:
            assert len(snap.per_core) == (snap.cores_logical or len(snap.per_core))
            for c in snap.per_core:
                if c.usage is not None:
                    assert 0.0 <= c.usage <= 100.0 + 40, c.usage
        return f"cpu={snap.name!r} usage={snap.usage:.1f}% cores={len(snap.per_core)} via={s.status_line()}"
    finally:
        s.stop()


def test_ntdll_cpu_source():
    from sysmon.sensors.cpu_ntdll import NtdllCpuSource
    n = NtdllCpuSource()
    if not n.available():
        return f"skipped: {n.reason}"
    n.sample()          # prime: the first call only establishes a baseline
    time.sleep(0.4)
    second = n.sample()
    if second is None or all(v is None for v in second):
        return "skipped: no deltas yet"
    for v in second:
        if v is not None:
            assert 0.0 <= v <= 100.0 + 5, v
    return f"ntdll per-core avg={NtdllCpuSource.average(second):.1f}% n={n._count}"


def test_memory_source():
    from sysmon.sensors.memory import MemorySource
    s = MemorySource()
    s.start()
    try:
        s.sample(); time.sleep(0.3)
        m = s.sample()
        if m is None:
            return "skipped: no sample"
        assert m.total_bytes and m.total_bytes > 0
        assert 0 <= m.usage <= 100.0, m.usage
        assert m.used_bytes + m.available_bytes == m.total_bytes, (m.used_bytes,
                                                                   m.available_bytes,
                                                                   m.total_bytes)
        if m.commit_limit_bytes:
            assert 0 <= m.commit_usage <= 100.0, m.commit_usage
            assert m.committed_bytes > 0, "committed bytes read as 0"
        return (f"mem total={m.total_bytes / 2**30:.1f}GiB used={m.used_bytes / 2**30:.1f}GiB "
                f"avail={m.available_bytes / 2**30:.1f}GiB load={m.usage:.0f}%")
    finally:
        s.stop()


def test_gpu_source():
    from sysmon.sensors.gpu import NvidiaSource
    s = NvidiaSource()
    s.start()
    try:
        if not s._ready:
            return f"skipped: {s.status_line() if hasattr(s, 'status_line') else s._reason}"
        gpus = s.sample()
        if not gpus:
            return "skipped: no devices"
        g = gpus[0]
        assert g.name, "GPU name empty"
        for label, val in (("usage", g.usage), ("temp", g.temperature_c),
                           ("power", g.power_w)):
            if val is not None:
                assert val >= 0, (label, val)
        if g.memory_total_bytes:
            assert 0 <= g.memory_usage <= 100.0, g.memory_usage
        return (f"gpu={g.name!r} util={g.usage}% temp={g.temperature_c}C "
                f"power={g.power_w}W vram={g.memory_used_bytes}/{g.memory_total_bytes} "
                f"clk={g.clock_mhz}/{g.clock_memory_mhz} driver={g.driver_version}")
    finally:
        s.stop()


def test_collector_lifecycle():
    c = Collector()
    c.start()
    try:
        c.warm(1, pause=0.3)
        snap = c.sample()
        assert snap is not None
        for _ in range(3):
            snap = c.sample()
        assert snap is not None
        return f"snapshot ok (cpu={snap.cpu.usage}, mem={snap.memory.usage}, gpus={len(snap.gpus)})"
    finally:
        c.stop()
        # stop() must be idempotent and safe to call twice
        c.stop()


def test_diagnose_report_runs():
    from sysmon.diagnose import report
    lines = report(None)
    assert lines
    joined = "\n".join(lines)
    assert "sysmon sensor diagnostics" in joined
    return f"{len(lines)} lines"


def test_optional_provider_never_crashes():
    """The LibreHardwareMonitor provider must degrade, never raise."""
    from sysmon.sensors import Capability
    from sysmon.sensors.thermal import ThermalPowerSource
    s = ThermalPowerSource()
    try:
        s.start()
    except Exception as exc:
        raise AssertionError(f"start() raised: {exc!r}")
    try:
        caps = s.capabilities()
        assert set(caps) == {Capability.CPU_TEMPERATURE, Capability.CPU_POWER}, caps
        result = s.sample()
        assert len(result) == 4, result
        for value in result:
            assert value is None or isinstance(value, float), value
        reason = s.status_line()
        assert isinstance(reason, str) and reason
        if s._ready:
            return f"optional provider active: {reason}"
        return f"optional provider unavailable (expected here): {reason[:80]}"
    finally:
        s.stop()


def test_optional_provider_stop_is_idempotent():
    from sysmon.sensors.thermal import ThermalPowerSource
    s = ThermalPowerSource()
    s.start()
    s.stop()
    s.stop()  # must not raise
    return "stop() is idempotent"


def test_cpu_falls_back_when_pdh_counters_missing(monkeypatch=None):
    """Force every PdhAddEnglishCounterW to fail and check the ntdll path takes over."""
    import sysmon.sensors.cpu as cpu_mod
    from sysmon.sensors.cpu import CpuSource

    original = cpu_mod._pdh.PdhAddEnglishCounterW
    cpu_mod._pdh.PdhAddEnglishCounterW = lambda *a, **k: 0xC0000BB9
    try:
        src = CpuSource()
        src.start()
        assert src._using_fallback, "should have switched to the ntdll fallback"
        assert src.status_line(), "status_line should explain the active path"
        src.sample()
        time.sleep(0.4)
        snap = src.sample()
        if snap is None or snap.usage is None:
            return "skipped: fallback produced no sample yet"
        assert len(snap.per_core) > 0
        src.stop()
        return f"fallback active: {src.status_line()[:70]}"
    finally:
        cpu_mod._pdh.PdhAddEnglishCounterW = original


def test_gpu_provider_degrades_without_nvml():
    """With nvml.dll unavailable the provider must report unavailable, not raise."""
    from sysmon.sensors.gpu import NvidiaSource

    original = ctypes.WinDLL
    ctypes.WinDLL = lambda *a, **k: (_ for _ in ()).throw(OSError("nvml.dll not found"))
    try:
        src = NvidiaSource()
        src.start()
        assert not src._ready
        from sysmon.sensors import Capability
        state = src.capabilities()[Capability.GPU]
        assert not state.ok and state.detail, state
        assert src.sample() == []
        src.stop()
        return f"degrades cleanly: {state.detail[:70]}"
    finally:
        ctypes.WinDLL = original


def test_cleaner_helpers():
    from sysmon.cleaner import is_admin, get_available_memory_bytes, CleanResult, clean_ram, get_cleanable_cache_bytes

    admin = is_admin()
    assert isinstance(admin, bool)
    avail = get_available_memory_bytes()
    if sys.platform == "win32":
        assert avail is not None and avail > 0, avail
        cleanable = get_cleanable_cache_bytes()
        assert cleanable is not None and cleanable >= 0, cleanable
    res = clean_ram()
    assert isinstance(res, CleanResult)
    assert isinstance(res.success, bool)
    assert isinstance(res.freed_bytes, int)
    assert isinstance(res.message, str)
    return f"cleaner helpers ok (admin={admin}, avail={avail // (1024**2)} MB)"


def test_cleaner_cli_parser():
    from sysmon.cli import build_parser

    p = build_parser()
    args1 = p.parse_args(["clean-ram"])
    assert args1.command == "clean-ram"
    assert args1.elevate is False

    args2 = p.parse_args(["clean-ram", "--elevate"])
    assert args2.command == "clean-ram"
    assert args2.elevate is True

    args3 = p.parse_args(["--clean-ram"])
    assert args3.clean_ram is True
    return "cleaner cli flags parsed ok"


def test_cpu_specs_lookup():
    from sysmon.sensors.cpu_specs import lookup_cpu_specs

    # AMD Ryzen 5000
    b, m = lookup_cpu_specs("AMD Ryzen 7 5700X 8-Core Processor", 3600.0)
    assert b == 3600.0 and m == 4600.0, (b, m)

    # AMD Zen 4
    b, m = lookup_cpu_specs("AMD Ryzen 7 7800X3D 8-Core Processor")
    assert b == 4200.0 and m == 5000.0, (b, m)

    # Intel Core
    b, m = lookup_cpu_specs("13th Gen Intel(R) Core(TM) i7-13700K")
    assert b == 3400.0 and m == 5400.0, (b, m)

    # Fallback with @ GHz
    b, m = lookup_cpu_specs("Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz")
    assert b == 2400.0 and m is None, (b, m)

    # Unknown
    b, m = lookup_cpu_specs("Custom Generic Processor", 2500.0)
    assert b == 2500.0 and m is None, (b, m)
    return "cpu specs lookup ok"



# ----------------------------------------------------------------- runner

def main() -> int:
    # Keep the runner usable on a legacy code page: never die while printing a failure.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # The GUI tests live in their own module and self-skip when PySide6 is absent.
    try:
        import test_gui
    except Exception:
        test_gui = None

    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    if test_gui is not None:
        tests += [(f"gui.{name}", fn) for name, fn in sorted(vars(test_gui).items())
                  if name.startswith("test_") and callable(fn)]
    passed = failed = skipped = 0
    failures = []
    for name, fn in tests:
        try:
            note = fn()
        except Exception:
            failed += 1
            failures.append((name, traceback.format_exc()))
            print(f"FAIL  {name}")
            continue
        if isinstance(note, str) and note.startswith("skipped"):
            skipped += 1
            print(f"SKIP  {name}: {note[9:]}")
        else:
            passed += 1
            suffix = f"  ({note})" if note else ""
            print(f"ok    {name}{suffix}")
    print()
    print(f"{passed} passed, {failed} failed, {skipped} skipped")
    for name, tb in failures:
        print(f"\n--- {name} ---\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
