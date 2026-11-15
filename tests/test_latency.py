"""Timing trace: text on screen -> voice heard."""
import time

from app import latency
from app.speech_queue import SpeechQueue
from tests.test_v6 import LINE, FakePlayer, FakeVS, wait_for


def test_trace_ends_at_first_audio(monkeypatch):
    got = []
    monkeypatch.setattr(latency, "on_result", lambda total, text: got.append((total, text)))
    monkeypatch.setattr(latency, "_ensure_writer", lambda: None)
    latency.changed()
    latency.mark("read")
    latency.audio_started()                # not armed yet: a previous line's audio
    assert got == []
    latency.speak("Hello there.")
    time.sleep(0.01)
    latency.audio_started()
    latency.audio_started()                # only once per line
    assert len(got) == 1 and got[0][1] == "Hello there." and got[0][0] >= 0.01
    line = latency._out.get_nowait()
    assert "read" in line and "speak" in line and "audio" in line


def test_new_change_replaces_unfinished_trace(monkeypatch):
    got = []
    monkeypatch.setattr(latency, "on_result", lambda total, text: got.append(text))
    monkeypatch.setattr(latency, "_ensure_writer", lambda: None)
    latency.changed()
    latency.speak("old")
    latency.changed()                      # next line appeared before the voice started
    latency.audio_started()
    assert got == []


def test_synth_reports_prefetch_reuse():
    vs, pl = FakeVS(delay=0.3), FakePlayer()
    q = SpeechQueue(vs, None, pl)
    try:
        how: list = []
        q._synth(vs, "v", {}, "Hi.", 1.0, how)
        assert how == ["made now"]
        how = []
        q._synth(vs, "v", {}, "Hi.", 1.0, how)
        assert how == ["ready"]
    finally:
        q.shutdown()


def test_prefetch_then_say_waits(monkeypatch):
    marks = []
    monkeypatch.setattr(latency, "mark", lambda stage, note="": marks.append((stage, note)))
    vs, pl = FakeVS(delay=0.3), FakePlayer()
    q = SpeechQueue(vs, None, pl)
    try:
        q.quick_steps = lambda: 4
        q.prefetch(LINE)
        time.sleep(0.05)
        q.say(LINE)
        assert wait_for(lambda: len(pl.got) == 2)
        assert ("voice", "waited for prefetch") in marks
    finally:
        q.shutdown()
