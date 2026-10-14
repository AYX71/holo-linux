"""Smoke test for holo_gui.py: drives MainWindow through a calibration and a
live tap without a microphone.

If PySide6 is installed this uses the real Qt (offscreen). If not, it uses a
stand-in for the Qt classes. The stand-in only catches mistakes in Holo's own
code (typos, wrong variable names, logic errors), NOT mistakes in how Qt is
used or how the window looks. Only a real run can show those.
"""
import os
import pathlib
import sys
import tempfile
import time
import types
import unittest

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import holo  # noqa: E402
import holo_gui_core as core  # noqa: E402
import test_synthetic as T  # noqa: E402

try:
    import PySide6  # noqa: F401
    REAL_QT = True
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
except ImportError:
    REAL_QT = False


def _install_fake_qt():
    class _Any:
        def __init__(self, *a, **k):
            pass

        def __call__(self, *a, **k):
            return _Any()

        def __getattr__(self, name):
            return _Any()

        def __or__(self, other):
            return self

    class _Meta(type):
        def __getattr__(cls, name):
            return _Any()

    class _Bound:
        def __init__(self):
            self.slots = []

        def connect(self, fn):
            self.slots.append(fn)

        def emit(self, *a):
            for fn in self.slots:
                fn(*a)

    class Signal:
        def __init__(self, *a):
            pass

        def __set_name__(self, owner, name):
            self.name = "_sig_" + name

        def __get__(self, obj, owner=None):
            if obj is None:
                return self
            return obj.__dict__.setdefault(self.name, _Bound())

    class _Base(metaclass=_Meta):
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return _Any()

    def cls(name, **attrs):
        return type(name, (_Base,), attrs)

    core_mod = types.ModuleType("PySide6.QtCore")
    core_mod.Signal = Signal
    for n in ("QRectF", "Qt", "QThread", "QTimer"):
        setattr(core_mod, n, cls(n))
    gui_mod = types.ModuleType("PySide6.QtGui")
    for n in ("QAction", "QColor", "QFont", "QIcon", "QPainter", "QPalette", "QPen", "QPixmap"):
        setattr(gui_mod, n, cls(n))
    w_mod = types.ModuleType("PySide6.QtWidgets")
    for n in ("QApplication", "QCheckBox", "QComboBox", "QGridLayout", "QHBoxLayout",
              "QLabel", "QLineEdit", "QMainWindow", "QMenu", "QMessageBox",
              "QPlainTextEdit", "QProgressBar", "QPushButton", "QTabWidget",
              "QVBoxLayout", "QWidget"):
        setattr(w_mod, n, cls(n))
    w_mod.QSystemTrayIcon = cls("QSystemTrayIcon",
                                isSystemTrayAvailable=staticmethod(lambda: False))
    pkg = types.ModuleType("PySide6")
    pkg.QtCore, pkg.QtGui, pkg.QtWidgets = core_mod, gui_mod, w_mod
    sys.modules.update({"PySide6": pkg, "PySide6.QtCore": core_mod,
                        "PySide6.QtGui": gui_mod, "PySide6.QtWidgets": w_mod})


class _DummyWorker:
    def stop(self):
        pass


class GuiFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = pathlib.Path(cls.tmp.name)
        cls.saved = (holo.DATA_DIR, holo.CONF_DIR, holo.PROFILE, holo.ACTIONS,
                     os.environ.get("XDG_CONFIG_HOME"))
        holo.DATA_DIR, holo.CONF_DIR = t / "data", t / "conf"
        holo.PROFILE, holo.ACTIONS = holo.DATA_DIR / "profile.json", holo.CONF_DIR / "actions.json"
        os.environ["XDG_CONFIG_HOME"] = str(t / "xdg")
        if REAL_QT:
            from PySide6.QtWidgets import QApplication
            cls.app = QApplication.instance() or QApplication([])
        else:
            _install_fake_qt()
        import holo_gui
        cls.gui = holo_gui
        quiet = types.SimpleNamespace(information=lambda *a, **k: None,
                                      warning=lambda *a, **k: None)
        holo_gui.QMessageBox = quiet       # a real modal dialog would hang the test
        cls.win = holo_gui.MainWindow()
        cls.win.worker, cls.win.worker_channels = _DummyWorker(), 1

    @classmethod
    def tearDownClass(cls):
        (holo.DATA_DIR, holo.CONF_DIR, holo.PROFILE, holo.ACTIONS, xdg) = cls.saved
        if xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = xdg
        cls.tmp.cleanup()

    def test_1_taps_before_calibration_do_not_crash(self):
        self.win.on_detected(T.one_window(T.resonant_tap("left_rear")))
        self.assertIsNone(self.win.model)

    def test_2_calibration_saves_a_profile(self):
        w = self.win
        w.start_calibration()
        self.assertIsNotNone(w.session)
        # taps before pressing Ready are ignored
        w.on_detected(T.one_window(T.resonant_tap(holo.ZONES[0])))
        self.assertEqual(len(w.session.samples[holo.ZONES[0]]), 0)
        for z in holo.ZONES:
            w.calibration_ready()
            for _ in range(holo.CAL_PER_ZONE):
                w.on_detected(T.one_window(T.resonant_tap(z)))
        self.assertIsNone(w.session)
        self.assertIsNotNone(w.model)
        self.assertTrue(holo.PROFILE.exists())

    def test_3_live_tap_runs_the_zone_action(self):
        w = self.win
        marker = pathlib.Path(self.tmp.name) / "fired"
        w._set_action("right_front", f"touch {marker}")
        ok = False
        for _ in range(6):
            holo._last_fired.clear()
            w.on_detected(T.one_window(T.resonant_tap("right_front")))
            for _ in range(20):
                if marker.exists():
                    ok = True
                    break
                time.sleep(0.1)
            if ok:
                break
        self.assertTrue(ok, "right_front tap never ran its action")

    def test_4_speech_like_sound_is_ignored(self):
        w = self.win
        marker = pathlib.Path(self.tmp.name) / "fired2"
        w._set_action("left_rear", f"touch {marker}")
        t = np.arange(holo.WIN) / holo.SR
        tone = (0.2 * np.sin(2 * np.pi * 400 * t)).astype("float32")
        self.assertIsNotNone(holo.gate(tone))
        holo._last_fired.clear()
        w.on_detected(tone)
        time.sleep(0.3)
        self.assertFalse(marker.exists())

    def test_5_actions_persist_and_autostart_toggles(self):
        w = self.win
        w._set_action("left_front", "echo hi")
        self.assertEqual(core.load_actions()["left_front"], "echo hi")
        w._set_autostart(True)
        self.assertTrue(core.autostart_enabled())
        w._set_autostart(False)
        self.assertFalse(core.autostart_enabled())

    def test_6_microphone_failure_is_handled(self):
        w = self.win
        w.worker = _DummyWorker()
        w.on_failed("boom")
        self.assertIsNone(w.worker)
        w.worker, w.worker_channels = _DummyWorker(), 1


if __name__ == "__main__":
    unittest.main()
