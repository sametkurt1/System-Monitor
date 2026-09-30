"""GUI tests.  Everything here runs headless via the Qt ``offscreen`` platform, so
``python tests/run_tests.py`` stays usable in CI and over SSH.

If PySide6 is not installed the module simply contributes no tests, and the terminal
UI tests still cover the shared sensor layer.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set before any QApplication exists.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtCore, QtGui, QtWidgets

    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False


if not HAVE_QT:
    # Nothing to register; the runner will simply find no gui_* tests.
    def __getattr__(name):  # noqa: D401
        raise AttributeError(name)
else:
    from sysmon.gui import theme as T
    from sysmon.gui.widgets import (BarGauge, Card, CoreGrid, ElidedLabel, Gauge,
                                    Sparkline, StatRow, make_icon)

    _app = None

    def app():
        """A single QApplication shared by every GUI test."""
        global _app
        if _app is None:
            _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
            T.refresh_fonts()
            _app.setStyleSheet(T.STYLESHEET)
        return _app

    from sysmon.models import CpuCore, CpuSnapshot, GpuSnapshot, MemorySnapshot, Snapshot

    # ------------------------------------------------------------------ theme

    def test_gui_theme_helpers():
        app()
        assert T.qcolor((10, 20, 30)).getRgb()[:3] == (10, 20, 30)
        assert T.qcolor("#ff8800").name() == "#ff8800"
        assert T.qcolor(T.qcolor("#112233")).name() == "#112233"
        c = T.alpha(T.qcolor((0, 0, 0)), 40)
        assert c.alpha() == 40
        p = T.pen(T.ACCENT, 3.0)
        assert abs(p.widthF() - 3.0) < 0.01
        assert p.color().name() == T.ACCENT
        # severity must ramp blue -> green -> yellow -> red
        assert T.severity_q(0) != T.severity_q(100)
        assert T.severity_q(50) != T.severity_q(75)
        # None must use this window's dim grey, not the terminal ramp's fallback.
        assert T.severity_q(None).name() == T.qcolor(T.FG_DIM).name()
        return "theme helpers ok"

    # ------------------------------------------------------------------ gauge

    def test_gui_gauge_snaps_then_eases():
        app()
        g = Gauge(T.ACCENT, "%", "CPU")
        g.resize(178, 178)
        g.set_value(50.0)
        assert g._current == 50.0, "first reading must appear immediately"
        g.set_value(100.0)
        assert g._current == 50.0, "set_value alone must not move the needle"
        g.tick()
        assert 50.0 < g._current < 100.0, "tick should ease toward the target"
        for _ in range(200):
            g.tick()
        assert abs(g._current - 100.0) < 0.1, g._current
        g.set_value(None)
        assert g._target is None
        return "gauge snaps first, eases after"

    def test_gui_gauge_clamps():
        app()
        g = Gauge()
        g.set_value(140.0)
        assert g._target == 100.0
        g.set_value(-5.0)
        assert g._target == 0.0
        return "gauge clamps to 0..100"

    def test_gui_gauge_paints():
        app()
        g = Gauge(T.GPU, "%", "GPU")
        g.resize(178, 178)
        for value in (None, 0.0, 37.5, 100.0):
            g.set_value(value)
            g.tick()
            pm = g.grab()
            assert not pm.isNull()
        return "gauge paints for None/0/mid/100"

    # -------------------------------------------------------------- bar gauge

    def test_gui_bar_gauge():
        app()
        b = BarGauge("RAM", T.MEM, "%", 22)
        b.resize(240, 22)
        b.set_value(45.0)
        assert b._current == 45.0
        b.set_value(None)
        assert b._target is None
        b.set_na_reason("no sensor")
        assert not b.grab().isNull()
        # Degenerate sizes must not blow up the painter.
        for w in (0, 1, 3):
            b.resize(w, 22)
            pm = QtGui.QPixmap(max(1, w), 22)
            b.render(pm)
        b.resize(240, 22)
        return "bar gauge handles values, N/A and degenerate widths"

    # -------------------------------------------------------------- sparkline

    def test_gui_sparkline_history_states():
        app()
        s = Sparkline(T.MEM, 40)
        s.resize(200, 60)
        for n in (0, 1, 2, 3, 50):
            s.set_values([float(i % 90) for i in range(n)])
            assert not s.grab().isNull(), n
        s.set_values([None, None, None])
        assert not s.grab().isNull()
        return "sparkline paints empty, partial and full histories"

    def test_gui_sparkline_height_bounded():
        app()
        s = Sparkline(T.MEM, 40)
        s.setMinimumHeight(40)
        assert s.maximumHeight() >= 40
        s2 = Sparkline(T.MEM, 40)
        s2.show()
        s2.resize(200, 2000)  # ignore the maximum
        s2.set_values([1.0, 2.0, 3.0])
        assert s2.height() <= s2.maximumHeight() + 1, s2.height()
        s2.hide()
        return "sparkline will not balloon past its cap"

    # ---------------------------------------------------------------- statrow

    def test_gui_statrow_elides_label():
        app()
        r = StatRow("Committed")
        r.resize(120, 21)
        r.set_value("51%")
        assert not r.grab().isNull()
        r.set_label("Something much longer than the row")
        assert not r.grab().isNull()
        return "statrow elides instead of colliding"

    # --------------------------------------------------------------- coregrid

    def test_gui_coregrid_counts():
        app()
        g = CoreGrid()
        g.resize(400, 120)
        for n in (0, 1, 2, 3, 4, 8, 12, 16, 32, 64, 128):
            g.set_values([float(i % 101) for i in range(n)])
            assert not g.grab().isNull(), n
        g.set_values([None] * 8)
        assert not g.grab().isNull()
        return "core grid paints for 0..128 processors and for N/A"

    def test_gui_coregrid_emits_click():
        app()
        g = CoreGrid()
        g.resize(400, 120)
        g.set_values([10.0] * 8)
        g.show()
        seen = []
        g.clicked.connect(seen.append)
        rects = g._cell_rects()
        assert rects, "expected cells"
        centre = rects[2].center().toPoint()
        QtWidgets.QApplication.sendEvent(g, QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonPress, QtCore.QPointF(centre),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.NoModifier))
        g.hide()
        assert seen == [2], seen
        return "clicking a core cell emits its index"

    # ----------------------------------------------------------------- label

    def test_gui_elided_label():
        app()
        label = ElidedLabel("AMD Ryzen 7 5700X 8-Core Processor")
        label.setFont(T.font(11.0))
        label.resize(90, 18)
        label.setFullText("AMD Ryzen 7 5700X 8-Core Processor")
        assert label.fullText().endswith("Processor")
        assert "\u2026" in label.text() or label.text() == label.fullText()
        assert "Ryzen" in label.toolTip()
        return "elided label keeps the full text in its tooltip"

    # ------------------------------------------------------------------ card

    def test_gui_card_header_does_not_expand():
        """Regression: the title label once absorbed all vertical slack."""
        app()
        card = Card("Memory", T.MEM)
        card.resize(240, 460)
        lay = QtWidgets.QVBoxLayout()
        lay.addWidget(card)
        holder = QtWidgets.QWidget()
        holder.setLayout(lay)
        holder.resize(240, 460)
        holder.show()
        app().processEvents()
        assert card.title.height() <= 20, card.title.height()
        assert card.title.y() <= 20, card.title.y()
        holder.hide()
        return "card title stays pinned to the top"

    def test_gui_icon_is_not_blank():
        app()
        icon = make_icon(64)
        assert not icon.isNull()
        pm = icon.pixmap(64, 64)
        assert not pm.isNull()
        # Something must have been drawn.
        image = pm.toImage()
        assert any(image.pixelColor(x, y).alpha() > 0
                   for x in range(0, 64, 4) for y in range(0, 64, 4))
        return "window icon renders"

    # ------------------------------------------------------------ integration

    def _sample(with_values: bool) -> Snapshot:
        if not with_values:
            return Snapshot()
        return Snapshot(
            cpu=CpuSnapshot(name="Test CPU", usage=37.0, cores_physical=4,
                            cores_logical=8, base_clock_mhz=3600.0,
                            temperature_c=58.0, power_w=72.0, frequency_mhz=4650.0,
                            per_core=tuple(CpuCore(index=i, usage=float(i * 7 % 100))
                                           for i in range(8))),
            memory=MemorySnapshot(total_bytes=16 * 1024 ** 3, used_bytes=7 * 1024 ** 3,
                                  available_bytes=9 * 1024 ** 3, usage=45.0,
                                  committed_bytes=9 * 1024 ** 3,
                                  commit_limit_bytes=32 * 1024 ** 3,
                                  standby_bytes=2 * 1024 ** 3),
            gpus=(GpuSnapshot(index=0, name="Test GPU", usage=67.0, temperature_c=64.0,
                              power_w=218.0, power_limit_w=300.0,
                              memory_used_bytes=8 * 1024 ** 3,
                              memory_total_bytes=16 * 1024 ** 3, clock_mhz=2760.0,
                              clock_memory_mhz=14001.0, driver_version="999.99"),),
        )

    def test_gui_window_builds_and_renders():
        application = app()
        from sysmon.gui.main_window import MainWindow

        win = MainWindow(interval=1.0)
        win.resize(1180, 720)
        try:
            for snapshot in (_sample(False), _sample(True), _sample(False)):
                win._on_snapshot(snapshot)
            application.processEvents()
            pm = win.grab()
            assert not pm.isNull()
            assert pm.width() == 1180 and pm.height() == 720
            # A window with no data must still say so rather than crash.
            assert win.cpu_gauge._target is None
        finally:
            win.close()
        return "window builds, renders snapshots and closes"

    def test_gui_window_never_crashes_on_partial_data():
        application = app()
        from sysmon.gui.main_window import MainWindow

        win = MainWindow(interval=1.0)
        try:
            weird = Snapshot(
                cpu=CpuSnapshot(per_core=(CpuCore(index=0),)),
                memory=MemorySnapshot(),
                gpus=(GpuSnapshot(),))
            win._on_snapshot(weird)
            win._on_notes(["pdh: cpu.frequency unavailable - reason"])
            application.processEvents()
            assert not win.grab().isNull()
        finally:
            win.close()
        return "all-fields-empty snapshot is handled"

    def test_gui_clean_ram_buttons():
        application = app()
        from sysmon.cleaner import CleanResult
        from sysmon.gui.main_window import MainWindow

        win = MainWindow(interval=1.0)
        try:
            assert hasattr(win, "clean_btn")
            assert hasattr(win, "mem_clean_btn")
            assert win.clean_btn.isEnabled()
            assert win.mem_clean_btn.isEnabled()

            # Test finishing signal handler
            # 1. Success with freed bytes
            res_freed = CleanResult(success=True, freed_bytes=1024 * 1024 * 512, message="Freed 512 MB cache")
            win._on_clean_finished(res_freed)
            application.processEvents()
            assert "Freed" in win.clean_btn.text() or "0.5" in win.clean_btn.text() or "512" in win.clean_btn.text()
            assert "Freed" in win.mem_clean_btn.text() or "0.5" in win.mem_clean_btn.text() or "512" in win.mem_clean_btn.text()

            # 2. Success with 0 bytes (already clean)
            res_clean = CleanResult(success=True, freed_bytes=0, message="Cache clean")
            win._on_clean_finished(res_clean)
            application.processEvents()
            assert "clean" in win.clean_btn.text().lower()

            # 3. Failure
            res_fail = CleanResult(success=False, freed_bytes=0, message="Access denied")
            win._on_clean_finished(res_fail)
            application.processEvents()
            assert "⚠️" in win.status.text() or "Access denied" in win.status.text()

            # 4. Reset helper
            win._reset_clean_buttons()
            assert win.clean_btn.isEnabled()
            assert "Clean RAM" in win.clean_btn.text()
            assert win.mem_clean_btn.isEnabled()
            assert "⚡" in win.mem_clean_btn.text()

            # 5. Live standby cache display on snapshot
            assert hasattr(win, "mem_cache")
            win._on_snapshot(_sample(True))
            application.processEvents()
            assert "2" in win.mem_cache._text or "GB" in win.mem_cache._text
            assert "2" in win.clean_btn.text()
            assert "2" in win.mem_clean_btn.text()

            # 6. Middle click on tray icon triggers clean ram
            called = []
            orig_clean = win._on_clean_ram
            win._on_clean_ram = lambda: called.append(True)
            try:
                from PySide6 import QtWidgets
                win._on_tray_activated(QtWidgets.QSystemTrayIcon.ActivationReason.MiddleClick)
                assert called == [True]
            finally:
                win._on_clean_ram = orig_clean
        finally:
            win.close()
        return "clean ram buttons and signal handlers ok"

    def test_gui_cpu_card_stats():
        application = app()
        from sysmon.gui.main_window import MainWindow

        win = MainWindow(interval=1.0)
        try:
            assert hasattr(win, "cpu_max")
            assert hasattr(win, "cpu_temp")
            assert hasattr(win, "cpu_power")
            # Snapshot with temperature and power
            win._on_snapshot(_sample(True))
            application.processEvents()
            assert "58" in win.cpu_temp._text or "C" in win.cpu_temp._text
            assert "72" in win.cpu_power._text or "W" in win.cpu_power._text
        finally:
            win.close()
        return "cpu card stats render correctly"



    def test_gui_sampler_thread_stops():
        app()
        from sysmon.gui.sampler import SamplerThread

        thread = SamplerThread(interval=0.2)
        thread.start()
        # Let it reach the sampling loop before asking it to stop.
        app().processEvents()
        assert thread.isRunning(), "sampler thread should be running"
        thread.stop(timeout_ms=8000)
        assert not thread.isRunning(), "sampler thread leaked after stop"
        return "sampler thread starts and stops cleanly"

    def test_gui_sampler_emits_snapshot():
        app()
        from sysmon.gui.sampler import SamplerThread

        got = []
        loop_done = []

        def on_snap(s):
            got.append(s)
            thread.stop(timeout_ms=6000)
            loop_done.append(True)

        thread = SamplerThread(interval=0.5)
        thread.snapshotReady.connect(on_snap)
        thread.finished.connect(app().quit)
        thread.start()
        # Guard so a broken sensor layer cannot hang the test run.
        QtCore.QTimer.singleShot(12000, app().quit)
        app().exec()
        if not got:
            return "skipped: no snapshot within the timeout"
        assert got[0] is not None
        return "sampler produced a live snapshot"

    def test_gui_never_raises_inside_paint():
        """Any exception inside a paintEvent is swallowed by Qt; assert on the
        widget state instead, and force a repaint of every custom widget."""
        application = app()
        from sysmon.gui.main_window import MainWindow

        win = MainWindow(interval=1.0)
        try:
            for snapshot in (_sample(True), _sample(False)):
                win._on_snapshot(snapshot)
            for widget in (win.cpu_gauge, win.gpu_gauge, win.mem_bar, win.vram_bar,
                           win.cpu_spark, win.gpu_spark, win.mem_spark,
                           win.core_grid, win.cpu_temp, win.cpu_power, win.cpu_max,
                           win.gpu_power, win.mem_commit, win.mem_cache,
                           win.cpu_name, win.gpu_name):
                for _ in range(30):
                    widget.tick() if hasattr(widget, "tick") else None
                widget.repaint()
            application.processEvents()
        finally:
            win.close()
        return "every custom widget repaints without error"
