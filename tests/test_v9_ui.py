import os
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

from app.config import NARRATOR, Config


@pytest.fixture(scope="module")
def app():
    from app.ui import theme
    a = QApplication.instance() or QApplication([])
    theme.apply(a)
    return a


@pytest.fixture()
def win(app):
    from app.ui.main_window import MainWindow
    cfg = Config(Path(tempfile.mkdtemp()) / "s.json")
    cfg.settings.muted = True
    cfg.settings.show_tray = False
    w = MainWindow(cfg)
    w.bank.enabled = lambda: False
    w.setWindowOpacity(0.0)           # a real window, but invisible during tests
    w.resize(1100, 720)
    w.show()
    app.processEvents()
    yield w
    w._quitting = True
    w.ocr.stop()
    w.hotkeys.clear()
    w.queue.shutdown()
    w.hide()


def test_icons_and_fonts(app):
    from app.ui import theme
    assert theme.load_fonts()
    assert theme.SANS == "Geist" and theme.MONO == "Geist Mono"
    d = theme.asset_dir("icons")
    names = [f[:-4] for f in os.listdir(d) if f.endswith(".svg")]
    assert len(names) >= 60
    for n in names:
        assert not theme.pixmap(n).isNull(), n
    # every icon the UI asks for exists
    import re
    used = set()
    for f in Path(theme.asset_dir()).parent.joinpath("app", "ui").glob("*.py"):
        used |= set(re.findall(r'''icon(?:_button)?\(\s*["']([a-z0-9-]+)["']''', f.read_text("utf-8")))
        used |= set(re.findall(r'''button\([^)]*?,\s*["']([a-z0-9-]+)["']''', f.read_text("utf-8")))
    missing = [u for u in used if u and "-" in u or u.isalpha()]
    missing = [u for u in missing if u not in names and not theme.has_icon(u) and len(u) > 2
               and u not in ("primary", "ghost", "danger", "icon")]
    assert not missing, missing


def test_pages_and_shortcuts(win, app):
    assert win.tabs.count() == 7
    for i, key in enumerate(["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5", "Ctrl+6"]):
        QTest.keySequence(win, key)
        app.processEvents()
        assert win.tabs.currentIndex() == i
    win.tabs.setCurrentWidget(win.settings_view)
    assert win.tabs.currentIndex() == 6
    win.tabs.setCurrentWidget(win.shots)
    assert win.tabs.currentWidget() is win.shots
    assert win.sidebar.items[1].isChecked()


def test_sidebar_and_inspector_toggle(win, app):
    win.sidebar.set_compact(True)
    assert win.sidebar.width() == 56 and not win.sidebar.items[0].text()
    win.sidebar.set_compact(False)
    assert win.sidebar.items[0].text() == "Read"
    was = win.inspector.isVisible()
    win._toggle_inspector()
    assert win.inspector.isVisible() != was and win.cfg.settings.inspector_open != was


def test_reading_flow_fills_transcript_cast_and_status(win, app):
    win._handle_reading(["Aiko", "Hello there, how are you today?"], None, None, False)
    app.processEvents()
    assert win.transcript.count() == 1
    assert "Aiko" in win.cfg.characters(win.game)
    assert win.history.count() == 1
    assert win.lbl_text.text().startswith("Hello there")
    win.chars.select("Aiko")
    assert win.chars.md.current() == "Aiko"


def test_themed_dialogs_return_values(win, app):
    from app.ui import components

    def answer(text=None, accept=True):
        def go():
            d = app.activeModalWidget()
            assert isinstance(d, QDialog)
            if text is not None:
                d.findChild(QLineEdit).setText(text)
            d.accept() if accept else d.reject()
        QTimer.singleShot(150, go)

    answer(accept=True)
    assert components.confirm(win, "Delete", "Sure?", "Delete", destructive=True)
    answer(accept=False)
    assert not components.confirm(win, "Delete", "Sure?")
    answer("Presenter")
    assert components.prompt(win, "Rename", "New name", "PRESENTER") == ("Presenter", True)


def test_rename_through_the_ui(win, app, monkeypatch):
    from app.ui import main_window
    win._handle_reading(["PRESENTER", "Welcome back, everyone!"], None, None, False)
    monkeypatch.setattr(main_window, "prompt", lambda *a, **k: ("Presenter", True))
    win._rename_character("PRESENTER")
    assert "Presenter" in win.cfg.characters(win.game)
    assert "Presenter" in win.chars.cards


def test_setup_dialog_uses_the_same_handlers(win, app):
    win._open_setup()
    app.processEvents()
    assert win.setup_dlg.isVisible()
    win.ed_profile.setText("Another Game")
    win.ed_profile.editingFinished.emit()
    assert win.game == "Another Game" and win.lbl_game.text() == "Another Game"
    win.setup_dlg.accept()


def test_wheel_never_changes_a_setting(win, app):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    win.tabs.setCurrentIndex(6)
    win.settings_view.show_section(1)
    app.processEvents()
    cb = win.settings_view.cb_engine
    before = cb.currentIndex()
    ev = QWheelEvent(QPointF(5, 5), cb.mapToGlobal(QPointF(5, 5)), QPoint(), QPoint(0, -120),
                     Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(cb, ev)
    assert cb.currentIndex() == before
