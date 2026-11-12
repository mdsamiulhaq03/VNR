import json
import tempfile
import threading
import time
from pathlib import Path

from app import presets, voices
from app.config import NARRATOR, Config
from app.speech_queue import Line, SpeechQueue
from app.tts.base import TTSEngine


def test_default_presets_3_male_8_female():
    ps = presets.defaults()
    assert sum(p["gender"] == "male" for p in ps) == 3
    assert sum(p["gender"] == "female" for p in ps) == 8
    assert len({p["id"] for p in ps}) == 11
    for p in ps:
        assert voices.instruct(voices.effective({"preset": p["id"]}, presets.by_id(ps)))


def test_eight_girls_get_eight_presets_then_custom():
    ps = presets.defaults()
    chars: dict = {}
    for i in range(9):
        chars[f"g{i}"] = voices.complete_character(f"g{i}", {"gender": "female"}, chars, ps)
    used = [c["preset"] for c in chars.values()]
    assert len(set(used[:8])) == 8 and all(used[:8])
    assert used[8] == ""                                   # 9th girl: custom voice
    assert chars["g8"]["gender"] == "female" and chars["g8"]["seed"]
    m = voices.complete_character("Kenji", {}, chars, ps)
    assert presets.by_id(ps)[m["preset"]]["gender"] == "male"


def test_accent_precedence():
    ps = presets.by_id(presets.defaults())
    rec = {"preset": "f-clara"}                            # british preset
    assert voices.effective(rec, ps)["accent"] == "british accent"
    assert voices.effective(rec, ps, "australian accent")["accent"] == "australian accent"
    rec["accent"] = "indian accent"                        # character accent beats preset
    assert voices.effective(rec, ps)["accent"] == "indian accent"
    assert voices.effective(rec, ps, "korean accent")["accent"] == "korean accent"
    whisper = voices.effective({"preset": "f-yuki"}, dict(ps, **{"f-yuki": dict(
        ps["f-yuki"], style="whisper")}))
    assert "whisper" in voices.instruct(whisper)


def test_voice_key_changes_with_design():
    ps = presets.by_id(presets.defaults())
    a = voices.effective({"preset": "m-ryan"}, ps)
    b = voices.effective({"preset": "m-ryan"}, ps, "british accent")
    assert voices.voice_key(a) != voices.voice_key(b)
    assert voices.voice_key(a) == voices.voice_key(dict(a))


def test_v3_records_get_presets():
    p = Path(tempfile.mkdtemp()) / "settings.json"
    p.write_text(json.dumps({"voices": {"g": {
        "Sayori": {"gender": "female", "gender_auto": False, "age": "young adult",
                   "pitch": "high pitch", "accent": "american accent", "seed": 5,
                   "vs_profile_id": "x", "voice_override": ""}}}}))
    cfg = Config(p)
    rec = cfg.character("g", "Sayori")
    assert rec["preset"].startswith("f-") and rec["gender_auto"] is False
    assert cfg.design("g", "Sayori")["label"] == presets.by_id(cfg.settings.presets)[rec["preset"]]["name"]
    assert cfg.character("g", NARRATOR)["preset"] == ""


def test_recent_games():
    cfg = Config(Path(tempfile.mkdtemp()) / "settings.json")
    cfg.touch_game("Old Game", "old.exe", "Old")
    time.sleep(0.01)
    cfg.touch_game("New Game", "new.exe", "New")
    assert [g for g, _ in cfg.recent_games()] == ["New Game", "Old Game"]
    assert cfg.recent_games()[0][1]["exe"] == "new.exe"


# ------------------------------------------------- every sentence = same clone
class RecVS(TTSEngine):
    name = "voicestudio"

    def __init__(self):
        self.calls = []

    def synth(self, text, voice, speed=1.0, **o):
        self.calls.append((voice, o.get("instruct")))
        return text.encode()


class Bank:
    def __init__(self):
        self.made = 0

    def cached(self, design):
        return "clone1" if self.made else ""

    def enabled(self):
        return True

    def get_profile(self, design):
        time.sleep(0.2)
        self.made += 1
        return "clone1"


class P:
    def __init__(self):
        self.n = 0
        self.ev = threading.Event()

    def enqueue_bytes(self, b):
        self.n += 1
        if self.n == 3:
            self.ev.set()

    def stop(self):
        pass


def test_all_sentences_use_the_same_clone():
    vs, bank, pl = RecVS(), Bank(), P()
    q = SpeechQueue(vs, None, pl, bank=bank)
    design = voices.effective({"preset": "f-mia"}, presets.by_id(presets.defaults()))
    q.say(Line("g", "Mia", "Good morning to you. I made you a big breakfast. Please hurry up now.",
               {}, "en", design))
    assert pl.ev.wait(3)
    q.shutdown()
    assert bank.made == 1
    assert [v for v, _ in vs.calls] == ["clone1"] * 3      # never the drifting raw design
