import json
import tempfile
import threading
from pathlib import Path

from app import voices
from app.config import NARRATOR, Config
from app.name_split import split_name
from app.speech_queue import Line, SpeechQueue
from app.tts.base import TTSEngine, TTSError


# ------------------------------------------------------------ name split
def test_name_on_top_line():
    assert split_name(["Sayori", "Hey, wait up for me!"], []) == ("Sayori", "Hey, wait up for me!")
    # "Mr." has a period: dialogue unless it's a known character
    assert split_name(["Mr. Tanaka", "Class is starting."], []) == \
        ("", "Mr. Tanaka Class is starting.")
    assert split_name(["Mr. Tanaka", "Class is starting."], ["Mr. Tanaka"]) == \
        ("Mr. Tanaka", "Class is starting.")


def test_wrapped_sentence_is_narration():
    assert split_name(["The morning air was cold and the street",
                       "was completely empty."], []) == \
        ("", "The morning air was cold and the street was completely empty.")


def test_inline_name_formats():
    assert split_name(["Monika: Hi there!"], []) == ("Monika", "Hi there!")
    assert split_name(["さより「おはよう！」"], []) == ("さより", "「おはよう！」")


def test_known_speaker_typo_and_short_exclamation():
    assert split_name(["Sayorl", "Sorry!"], ["Sayori"]) == ("Sayori", "Sorry!")
    assert split_name(["Hey!", "Over here."], []) == ("", "Hey! Over here.")


def test_modes():
    assert split_name(["Hey!", "Over here."], [], "always") == ("Hey!", "Over here.")
    assert split_name(["Sayori", "Hi!"], [], "never") == ("", "Sayori Hi!")


# ---------------------------------------------------------------- gender
def test_guess_gender():
    for n in ("Emily", "Sakura", "Monika", "Yuri", "Natsuki"):
        assert voices.guess_gender(n) == "female", n
    for n in ("John", "Hiroshi", "Kenji", "Makoto Naegi"):
        assert voices.guess_gender(n) in ("male", "female"), n
    assert voices.guess_gender("John") == "male"
    assert voices.guess_gender("Hiroshi") == "male"
    assert voices.guess_gender("Xq") == "unknown"
    assert voices.guess_gender(NARRATOR) == "unknown"






def test_config_migrates_string_voices():
    p = Path(tempfile.mkdtemp()) / "settings.json"
    p.write_text(json.dumps({"voices": {"g": {"(narrator)": "default", "Sayori": "Zira",
                                              "Bob": "sapi:Microsoft David"}}}))
    cfg = Config(p)
    s = cfg.character("g", "Sayori")
    assert s["gender"] == "female" and s["voice_override"] == "vs:Zira"
    assert cfg.character("g", "Bob")["voice_override"] == "sapi:Microsoft David"
    assert cfg.character("g", NARRATOR)["voice_override"] == ""
    assert "seed" in cfg.character("g", "New Person")


# --------------------------------------------------------------- routing
class FailingVS(TTSEngine):
    name = "voicestudio"

    def __init__(self):
        self.calls = []

    def synth(self, text, voice, speed=1.0, **opts):
        self.calls.append((voice, opts))
        raise TTSError("boom")


class FakeSapi(TTSEngine):
    name = "sapi"

    def __init__(self):
        self.calls = []

    def voice_for_gender(self, gender, seed=0):
        return {"female": "sapi:Zira", "male": "sapi:David"}.get(gender, "sapi:Zira")

    def synth(self, text, voice, speed=1.0, **opts):
        self.calls.append((voice, opts))
        return text.encode()


class FakePlayer:
    def __init__(self):
        self.ev = threading.Event()

    def enqueue_bytes(self, b):
        self.ev.set()

    def stop(self):
        pass


def test_falls_back_to_same_gender_windows_voice():
    vs, sapi, pl = FailingVS(), FakeSapi(), FakePlayer()
    q = SpeechQueue(vs, sapi, pl)
    rec = {"gender": "male", "age": "young adult", "pitch": "low pitch", "accent": "Auto",
           "seed": 12}
    q.say(Line("g", "Bob", "Hello there friend.", rec, "en"))
    assert pl.ev.wait(2)
    q.shutdown()
    voice, opts = vs.calls[0]
    assert voice == "default" and opts["instruct"] == "male, young adult, low pitch"
    assert opts["seed"] == 12 and opts["language"] == "en"
    assert sapi.calls[0][0] == "sapi:David" and sapi.calls[0][1]["pitch"] < 0

