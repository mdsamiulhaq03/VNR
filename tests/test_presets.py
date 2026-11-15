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


def test_default_presets_60_in_12_categories():
    ps = presets.defaults()
    assert len(ps) == 60
    assert len({p["id"] for p in ps}) == 60 and len({p["seed"] for p in ps}) == 60
    by_cat: dict = {}
    for p in ps:
        assert p["age"] in ("young adult", "middle-aged", "elderly")   # adults only
        assert (p.get("vibe") or "") in ("",) + tuple(VIBE_SCRIPTS)
        assert p["vibe"] == "" or p["vibe"] in presets.VIBE_SAMPLES
        assert voices.instruct(voices.effective({"preset": p["id"]}, presets.by_id(ps)))
        assert p["best_for"] and p["category"] in presets.CATEGORY
        by_cat.setdefault(p["category"], []).append(p)
    assert set(by_cat) == {c["key"] for c in presets.CATEGORIES}
    for cat, items in by_cat.items():
        assert len(items) == 5, cat
        designs = {(p["age"], p["pitch"], p["accent"], p["style"]) for p in items}
        assert len(designs) == 5, f"two {cat} voices share a design"
        g = presets.CATEGORY[cat]["gender"]
        assert g == "any" or all(p["gender"] == g for p in items)
    assert sum(p.get("style") == "whisper" for p in by_cat["shy"]) <= 1


def test_new_characters_get_varied_voices_then_custom():
    ps = presets.defaults()
    chars: dict = {}
    n_female = sum(p["gender"] == "female" and p["category"] != "narrator" for p in ps)
    for i in range(n_female + 1):
        chars[f"g{i}"] = voices.complete_character(f"g{i}", {"gender": "female"}, chars, ps)
    used = [c["preset"] for c in chars.values()]
    cats = [presets.by_id(ps)[u]["category"] for u in used[:7]]
    assert len(set(cats)) == 7                             # one from each category first
    assert len(set(used[:n_female])) == n_female and all(used[:n_female])
    assert used[n_female] == ""                            # all taken: custom voice
    last = chars[f"g{n_female}"]
    assert last["gender"] == "female" and last["seed"] and last["vibe"]
    m = voices.complete_character("Kenji", {}, chars, ps)
    assert presets.by_id(ps)[m["preset"]]["gender"] == "male"
    whisper = [u for u in used[:n_female] if presets.by_id(ps)[u].get("style") == "whisper"]
    assert set(used[n_female - len(whisper):n_female]) == set(whisper)   # whispers last


def test_role_and_narrator_choose_the_category():
    ps = presets.defaults()
    pid = voices.pick_preset("female", {}, ps, role="tomboy")
    assert presets.by_id(ps)[pid]["category"] == "energetic"
    rec = voices.complete_character("Aida", {"gender": "female", "role": "boss"}, {}, ps)
    assert presets.by_id(ps)[rec["preset"]]["category"] == "mature" and rec["role"] == "boss"
    n = voices.complete_character(NARRATOR, {}, {}, ps)
    assert presets.by_id(ps)[n["preset"]]["category"] == "narrator"


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
    narrator = cfg.character("g", NARRATOR)["preset"]
    assert presets.by_id(cfg.settings.presets)[narrator]["category"] == "narrator"


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


# ------------------------------------------------------------ per-character age
def test_age_override_on_preset_and_custom():
    ps = presets.by_id(presets.defaults())
    base = voices.effective({"preset": "f-natasha"}, ps)
    older = voices.effective({"preset": "f-natasha", "age_override": "middle-aged"}, ps)
    assert base["age"] == "young adult" and older["age"] == "middle-aged"
    assert voices.voice_key(base) != voices.voice_key(older)
    custom = {"preset": "", "gender": "female", "age": "young adult", "seed": 5,
              "age_override": "elderly"}
    assert voices.effective(custom, ps)["age"] == "elderly"
    assert "child" not in voices.ADULT_AGES and "teenager" not in voices.ADULT_AGES


def test_cast_card_has_an_age_choice():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from app.ui.characters_view import CharacterCard
    app = QApplication.instance() or QApplication([])
    ps = presets.by_id(presets.defaults())
    rec = {"gender": "female", "preset": "f-natasha", "voice_override": "", "lines": 1}
    card = CharacterCard("Natasha", rec, [("♀  Natasha", "preset:f-natasha")])
    card.update_rec(rec, voices.effective(rec, ps))
    items = [card.cb_age.itemData(i) for i in range(card.cb_age.count())]
    assert items == ["", "young adult", "middle-aged", "elderly"]
    assert card.cb_age.itemText(0) == "Voice's own age (young adult)"
    assert card.cb_age.isEnabled()
    got = []
    card.changed.connect(lambda name, ch: got.append((name, ch)))
    card.cb_age.setCurrentIndex(2)
    assert got == [("Natasha", {"age_override": "middle-aged"})]
    card.update_rec(dict(rec, voice_override="sapi:Zira"), {})
    assert not card.cb_age.isEnabled()                 # a fixed voice has no age to set
    assert app
