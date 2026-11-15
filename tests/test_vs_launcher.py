"""Starting VoiceStudio automatically, and the status light's busy / error state."""
import threading
import time

import pytest

from app import vs_launcher
from app.tts.base import TTSError
from app.tts.voicestudio import VoiceStudioEngine
from tests.test_voicestudio_gate import FakeSession, Resp


@pytest.fixture()
def fake(monkeypatch, tmp_path):
    exe = tmp_path / "VoiceStudio.exe"
    exe.write_text("x")
    state = {"running": False, "launched": 0}
    monkeypatch.delenv("VNR_NO_AUTOSTART", raising=False)
    monkeypatch.setattr(vs_launcher, "default_exe", lambda: str(exe))
    monkeypatch.setattr(vs_launcher, "is_running", lambda name="": state["running"])

    def launch(path):
        state["launched"] += 1
        state["running"] = True
        return True
    monkeypatch.setattr(vs_launcher, "launch", launch)
    clock = {"t": 1000.0}
    return state, clock, vs_launcher.AutoStarter(now=lambda: clock["t"])


def test_starts_once_then_waits(fake):
    state, clock, st = fake
    url = "http://127.0.0.1:3900"
    assert st.maybe_start(url) == "started" and state["launched"] == 1
    assert st.starting
    clock["t"] += 30
    assert st.maybe_start(url) == "recent" and state["launched"] == 1   # not twice
    clock["t"] += vs_launcher.GIVE_UP_AFTER
    assert st.timed_out()


def test_already_open_is_not_started_again(fake):
    state, _clock, st = fake
    state["running"] = True
    assert st.maybe_start("http://localhost:3900") == "running" and state["launched"] == 0


def test_only_for_this_pc_and_when_wanted(fake):
    state, _clock, st = fake
    assert st.maybe_start("http://192.168.1.20:3900") == "not-local"
    assert st.maybe_start("http://127.0.0.1:3900", enabled=False) == "off"
    assert state["launched"] == 0


def test_not_installed(fake, monkeypatch):
    _state, _clock, st = fake
    monkeypatch.setattr(vs_launcher, "default_exe", lambda: "")
    assert st.maybe_start("http://127.0.0.1:3900") == "not-found"


def test_ready_clears_starting(fake):
    _state, _clock, st = fake
    st.maybe_start("http://127.0.0.1:3900")
    st.ready()
    assert not st.starting and not st.timed_out()


def test_engine_reports_busy_queue_and_last_error():
    s = FakeSession(delay=0.15)
    e = VoiceStudioEngine()
    e._session = s
    ts = [threading.Thread(target=lambda i=i: e.synth(f"l{i}", "v")) for i in range(3)]
    for t in ts:
        t.start()
    time.sleep(0.05)
    assert e.busy and e.queued == 2
    for t in ts:
        t.join(5)
    assert not e.busy and e.queued == 0
    s.codes = [500]
    with pytest.raises(TTSError):
        e.synth("x", "v")
    assert "HTTP 500" in e.last_error and e.last_error_at > 0


def test_health_info_is_kept():
    class S:
        def get(self, *a, **k):
            r = Resp(200, b'{"status":"ok","device":"cuda (RTX)","version":"0.5.8"}',
                     "application/json")
            r.json = lambda: {"status": "ok", "device": "cuda (RTX)", "version": "0.5.8"}
            return r
    e = VoiceStudioEngine()
    e._session = S()
    assert e.available() and e.info["device"] == "cuda (RTX)"
