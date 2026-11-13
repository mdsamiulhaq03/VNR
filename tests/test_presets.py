import json
import tempfile
import threading
import time
from pathlib import Path

from app import presets, voices
from app.config import NARRATOR, Config
from app.speech_queue import Line, SpeechQueue
from app.tts.base import TTSEngine
from app.voice_bank import VIBE_SCRIPTS


def test_default_presets_4_male_15_female_adults():
    ps = presets.defaults()
    assert sum(p["gender"] == "male" for p in ps) == 4
    assert sum(p["gender"] == "female" for p in ps) == 15
    assert len({p["id"] for p in ps}) == 19 and len({p["seed"] for p in ps}) == 19
    for p in ps:
        assert p["age"] in ("young adult", "middle-aged")          # adults only
        assert (p.get("vibe") or "") in ("",) + tuple(VIBE_SCRIPTS)
        assert voices.instruct(voices.effective({"preset": p["id"]}, presets.by_id(ps)))


def test_girls_get_every_preset_then_custom():
    ps = presets.defaults()
    chars: dict = {}
    for i in range(16):
        chars[f"g{i}"] = voices.complete_character(f"g{i}", {"gender": "female"}, chars, ps)
    used = [c["preset"] for c in chars.values()]
    assert len(set(used[:15])) == 15 and all(used[:15])
    assert used[15] == ""                                  # 16th girl: custom voice
    assert chars["g15"]["gender"] == "female" and chars["g15"]["seed"]
    assert chars["g15"]["vibe"]                            # custom girls get a vibe too
    m = voices.complete_character("Kenji", {}, chars, ps)
    assert presets.by_id(ps)[m["preset"]]["gender"] == "male"


def test_accent_precedence():
    ps = presets.by_id(presets.defaults())
    rec = {"preset": "f-chloe"}                            # british preset
    assert voices.effective(rec, ps)["accent"] == "british accent"
    assert voices.effective(rec, ps, "australian accent")["accent"] == "australian accent"
    rec["accent"] = "indian accent"                        # character accent beats preset
    assert voices.effective(rec, ps)["accent"] == "indian accent"
    assert voices.effective(rec, ps, "korean accent")["accent"] == "korean accent"
    assert "whisper" in voices.instruct(voices.effective({"preset": "f-yumi"}, ps))


def test_vibe_is_part_of_the_voice():
    ps = presets.by_id(presets.defaults())
    d = voices.effective({"preset": "f-natasha"}, ps)
    assert d["vibe"] == "sultry" and voices.voice_key(d).endswith("|sultry")
    plain = voices.effective({"preset": "m-ryan"}, ps)
    assert voices.voice_key(plain).count("|") == 1         # no vibe: old key format


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
    design = voices.effective({"preset": "f-bella"}, presets.by_id(presets.defaults()))
    q.say(Line("g", "Mia", "Good morning to you. I made you a big breakfast. Please hurry up now.",
               {}, "en", design))
    assert pl.ev.wait(3)
    q.shutdown()
    assert bank.made == 1
    assert [v for v, _ in vs.calls] == ["clone1"] * 3      # never the drifting raw design
