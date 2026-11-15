import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.config import Config


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
    cfg.settings.show_chat_head = False
    w = MainWindow(cfg)
    w.bank.enabled = lambda: False
    w.setWindowOpacity(0.0)
    w.show()
    app.processEvents()
    yield w
    w._quitting = True
    w.ocr.stop()
    w.hotkeys.clear()
    w.queue.shutdown()
    w.chat_head.set_shown(False)
    w.hide()


def test_setting_shows_and_hides_the_bubble(win, app):
    ch = win.chat_head
    assert not ch.isVisible()
    win.settings_view.cb_chat_head.setChecked(True)
    app.processEvents()
    assert ch.isVisible() and win.cfg.settings.show_chat_head
    flags = ch.windowFlags()
    assert flags & Qt.WindowStaysOnTopHint and flags & Qt.FramelessWindowHint
    assert flags & Qt.WindowDoesNotAcceptFocus
    ch._fire("hide")                         # right-click → Hide bubble
    app.processEvents()
    assert not ch.isVisible() and not win.cfg.settings.show_chat_head
    assert not win.settings_view.cb_chat_head.isChecked()


def test_state_follows_the_app(win, app):
    ch = win.chat_head
    win._toggle_pause()
    assert ch.paused and ch.pill.buttons["pause"].toolTip().startswith("Resume")
    win._toggle_pause()
    assert not ch.paused
    win._mute(False)
    assert not ch.muted and not ch.pill.buttons["mute"].isChecked()
    win._mute(True)
    assert ch.muted and ch.pill.buttons["mute"].isChecked()
    win.cb_auto.setChecked(True)
    assert ch.auto and ch.pill.buttons["auto"].isChecked()
    win._show_line("Sayori", "Hi!", {"gender": "female"}, {"label": "x"})
    assert ch.speaker == "Sayori" and "Sayori" in ch.toolTip()


def test_pill_buttons_run_the_controls(win, app, monkeypatch):
    calls = []
    for name in ("_toggle_ocr", "_toggle_pause", "_stop_voice", "_toggle_auto"):
        monkeypatch.setattr(win, name, lambda n=name: calls.append(n))
    monkeypatch.setattr(win.queue, "replay", lambda: calls.append("replay"))
    b = win.chat_head.pill.buttons
    for key in ("toggle", "pause", "replay", "stop", "auto"):
        QTest.mouseClick(b[key], Qt.LeftButton)
    assert calls == ["_toggle_ocr", "_toggle_pause", "replay", "_stop_voice", "_toggle_auto"]
    was = win.cfg.settings.muted
    QTest.mouseClick(b["mute"], Qt.LeftButton)
    assert win.cfg.settings.muted is (not was) and win.btn_mute.isChecked() is (not was)


def test_drag_snaps_to_an_edge_and_is_remembered(win, app):
    ch = win.chat_head
    ch.set_shown(True)
    ag = QGuiApplication.primaryScreen().availableGeometry()
    ch.move(ag.left() + ag.width() // 4, ag.bottom() + 500)    # left half, below the screen
    ch.snap(animate=False)
    assert ch.x() == ag.left() + ch.EDGE
    assert ag.top() <= ch.y() and ch.y() + ch.height() <= ag.bottom() + 1
    assert win.cfg.settings.chat_head_pos == [ch.x(), ch.y()]
    ch.move(ag.right() - ag.width() // 4, ag.top() + 100)      # right half
    ch.snap(animate=False)
    assert ch.x() + ch.width() == ag.right() + 1 - ch.EDGE


def test_click_opens_the_pill_and_a_drag_does_not(win, app):
    ch = win.chat_head
    ch.set_shown(True)
    ch.place(None)
    app.processEvents()
    QTest.mouseClick(ch, Qt.LeftButton, pos=QPoint(36, 36))
    assert ch.pill.isVisible()
    # The pill opens on the side facing the screen (the bubble starts at the right edge).
    assert ch.pill.geometry().right() < ch.geometry().center().x()
    QTest.mouseClick(ch, Qt.LeftButton, pos=QPoint(36, 36))
    assert not ch.pill.isVisible()
    start = ch.pos()
    QTest.mousePress(ch, Qt.LeftButton, pos=QPoint(36, 36))
    QTest.mouseMove(ch, QPoint(36, 136))
    QTest.mouseRelease(ch, Qt.LeftButton, pos=QPoint(36, 136))
    assert not ch.pill.isVisible() and ch.pos() != start
