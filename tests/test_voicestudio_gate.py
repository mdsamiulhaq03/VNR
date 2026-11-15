"""VoiceStudio answers 429 when busy: VN Reader sends one request at a time (the line
being read first), retries a 429, and doesn't give up on a voice because of one."""
import threading
import time

from app.tts import voicestudio
from app.tts.base import TTSError
from app.tts.voicestudio import VoiceStudioEngine, is_temporary


class Resp:
    def __init__(self, code, content=b"RIFFwav", ctype="audio/wav"):
        self.status_code, self.content, self.text = code, content, ""
        self.headers = {"content-type": ctype}


class FakeSession:
    """Like VoiceStudio: 429 if a request arrives while another is running."""
    def __init__(self, delay=0.05, codes=None):
        self.delay, self.codes = delay, list(codes or [])
        self.running, self.overlaps, self.order = 0, 0, []
        self._lock = threading.Lock()

    def post(self, url, json=None, **kw):
        with self._lock:
            self.running += 1
            busy = self.running > 1
            self.overlaps += busy
        try:
            if busy:
                return Resp(429)
            time.sleep(self.delay)
            self.order.append((json or {}).get("input"))
            return Resp(self.codes.pop(0)) if self.codes else Resp(200)
        finally:
            with self._lock:
                self.running -= 1


def _engine(session):
    e = VoiceStudioEngine()
    e._session = session
    return e


def test_requests_never_overlap():
    s = FakeSession()
    e = _engine(s)
    out = []
    ts = [threading.Thread(target=lambda i=i: out.append(e.synth(f"line {i}", "v")))
          for i in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert s.overlaps == 0 and len(out) == 5


def test_line_goes_before_background_work():
    s = FakeSession(delay=0.1)
    e = _engine(s)

    def bg(name):
        with e.background():
            e.synth(name, "v")
    first = threading.Thread(target=bg, args=("bg-1",))
    first.start()
    time.sleep(0.03)                               # bg-1 is now running
    later = threading.Thread(target=bg, args=("bg-2",))
    later.start()
    time.sleep(0.01)
    line = threading.Thread(target=lambda: e.synth("line", "v"))
    line.start()
    for t in (first, later, line):
        t.join(10)
    assert s.order == ["bg-1", "line", "bg-2"]


def test_busy_answer_is_retried(monkeypatch):
    monkeypatch.setattr(voicestudio.time, "sleep", lambda s: None)
    s = FakeSession(delay=0, codes=[429, 429, 200])
    assert _engine(s).synth("hi", "v") == b"RIFFwav"


def test_busy_too_long_raises(monkeypatch):
    monkeypatch.setattr(voicestudio.time, "sleep", lambda s: None)
    monkeypatch.setattr(voicestudio, "BUSY_WAIT_FG", 0.0)
    s = FakeSession(delay=0, codes=[429] * 5)
    try:
        _engine(s).synth("hi", "v")
        raise AssertionError("should have raised")
    except TTSError as err:
        assert is_temporary(err)


def test_busy_voicestudio_is_not_a_failed_voice():
    """A 429 while making a voice: tried again next time, not given up until restart."""
    from app import presets, voices
    from tests.test_emotion import BankVS, _bank

    class BusyOnce(BankVS):
        busy = 1

        def create_clone_profile(self, *a, **k):
            if self.busy:
                self.busy -= 1
                raise TTSError("VoiceStudio HTTP 429: still busy")
            return super().create_clone_profile(*a, **k)

    c, _vs, bank, _ready = _bank()
    bank.vs = BusyOnce()
    design = voices.effective({"preset": "f-natasha"}, presets.by_id(c.settings.presets))
    assert bank.get_profile(design) == ""               # busy this time
    assert voices.voice_key(design) not in bank._failed
    assert bank.get_profile(design)                     # works the next time


def test_temporary_errors():
    assert is_temporary("VoiceStudio HTTP 429: busy")
    assert is_temporary(TTSError("VoiceStudio unreachable: refused"))
    assert not is_temporary("every take came out whispery or squeaky")
