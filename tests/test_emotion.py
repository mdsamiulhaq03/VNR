"""Stage directions, moods and emotional voice variants."""
import json
import tempfile
import threading
import time
from pathlib import Path

from app import emotion, presets, voices
from app.config import Config
from app.speech_queue import Line, SpeechQueue
from app.tts.base import TTSEngine
from app.voice_bank import MOOD_SCRIPTS, VoiceBank

TAGS = {"[laughter]", "[sigh]", "[surprise-ah]", "[question-ah]", "[dissatisfaction-hnn]"}


def p(text, **kw):
    kw.setdefault("tags", TAGS)
    return emotion.parse(text, **kw)


# ------------------------------------------------------------------ parser
def test_the_natasha_line():
    d = p("*Seductively* And this might end up being very fun.")
    assert d.spoken == "And this might end up being very fun…"
    assert (d.mood, d.variant) == ("seductive", "seductive") and d.speed < 1


def test_ocr_lost_an_asterisk():
    assert p("Seductively* And this might end up being very fun.").mood == "seductive"
    assert "Seductively" not in p("Seductively* And this might be fun.").spoken
    d = p("That was fun. *winks")
    assert d.spoken == "That was fun…" and d.mood == "seductive"


def test_direction_only_line_says_nothing():
    assert p("*walks over*").spoken == ""
    assert p("*sighs*").spoken == ""                  # a lone tag isn't worth a request


def test_tag_goes_where_the_direction_was():
    d = p("Hi! *giggles* You're cute.")
    assert d.spoken == "Hi! [laughter] You're cute." and d.mood == "laugh"
    assert p("(sighs) Fine.").spoken == "[sigh] Fine."
    assert p("Hi! *giggles* You're cute.", tags=None).spoken == "Hi! You're cute."


def test_parentheses_and_emphasis_are_kept():
    assert p("I think (I think) so.").spoken == "I think (I think) so."
    assert p("I *really* like you.").spoken == "I really like you."


def test_fullwidth_asterisks():
    d = p("＊blushes＊ Th-thanks…")
    assert d.spoken == "Th-thanks…" and d.mood == "shy" and d.variant == ""


def test_vocalized_actions():
    d = p("*moans softly* Don't stop, please.")
    assert d.spoken.startswith("Mmmh… Don't stop") and d.mood == "aroused"
    assert d.variant == "aroused" and d.speed < 0.9
    assert p("*kisses you*").spoken == "Mwah."
    assert p("*moans loudly* Yes.").spoken == "Ahhn! Yes!"
    d = p("*moans softly* Ok.", vocalize=False)
    assert d.spoken == "Ok…" and d.mood == "aroused"
    # "pants" the clothing isn't panting
    assert p("*takes off her pants* There.").spoken == "There."


def test_highest_priority_mood_wins():
    d = p("*bites her lip and smiles seductively* Come here.")
    assert d.mood == "seductive" and d.spoken == "Come here…"


def test_dialogue_cues():
    d = p("Hey there~ ♥")
    assert d.mood == "seductive" and "~" not in d.spoken and "♥" not in d.spoken
    assert p("Hey~").mood == "playful"
    d = p("Ahn… nngh… there…")
    assert d.mood == "aroused" and d.spoken.startswith("Ahn… nngh")
    assert p("I-I didn't mean it…").mood == "shy"
    d = p("(She's so beautiful…)")
    assert d.spoken == "She's so beautiful…" and d.mood == "thought" and d.variant == ""
    d = p("WHAT ARE YOU DOING?!")
    assert d.spoken == "What are you doing?!" and d.mood == "angry"


def test_common_adverbs():
    for adv, mood in [("Teasingly", "seductive"), ("Playfully", "playful"),
                      ("Lovingly", "tender"), ("Nervously", "shy"), ("Sadly", "sad"),
                      ("Angrily", "angry"), ("Coldly", "cold"), ("Whispering", "whisper"),
                      ("Breathlessly", "aroused"), ("Happily", "happy"),
                      ("Sternly", "dominant"), ("Pleadingly", "begging")]:
        assert p(f"*{adv}* Okay.").mood == mood, adv
    assert p("*Angrily* Get out.").spoken == "Get out!"


def test_acting_off_still_strips():
    d = emotion.parse("*Seductively* Ok.", act=False)
    assert d.spoken == "Ok." and d.variant == "" and d.speed == 1.0


def test_plain_lines_untouched():
    for s in ("Good morning to you.", "first line here", "The heater was on."):
        d = p(s)
        assert d.spoken == s and d.mood == "" and d.speed == 1.0


# ------------------------------------------------------------ speech queue
class RecVS(TTSEngine):
    name = "voicestudio"

    def __init__(self):
        self.calls = []

    def synth(self, text, voice, speed=1.0, **o):
        self.calls.append((voice, text, speed))
        return text.encode()


class Player:
    def __init__(self):
        self.ev = threading.Event()

    def enqueue_bytes(self, b):
        self.ev.set()

    def stop(self):
        pass


class FakeBank:
    def __init__(self, variants):
        self.variants = variants
        self.asked = []

    def cached(self, design):
        return "neutral"

    def enabled(self):
        return True

    def variant(self, design, mood):
        self.asked.append(mood)
        return self.variants.get(mood, "")


def _say(bank, text, **hooks):
    vs, pl = RecVS(), Player()
    q = SpeechQueue(vs, None, pl, bank=bank)
    for k, v in hooks.items():
        setattr(q, k, v)
    design = voices.effective({"preset": "f-natasha"}, presets.by_id(presets.defaults()))
    q.say(Line("g", "Natasha", text, {}, "en", design))
    assert pl.ev.wait(3)
    q.shutdown()
    return vs.calls


def test_queue_uses_the_mood_variant():
    bank = FakeBank({"seductive": "sexy-clone"})
    calls = _say(bank, "*Seductively* And this might end up being very fun.")
    voice, text, speed = calls[0]
    assert voice == "sexy-clone" and "Seductively" not in text and speed < 1
    assert bank.asked == ["seductive"]


def test_queue_falls_back_to_the_normal_clone():
    bank = FakeBank({})                                      # variant not made yet
    calls = _say(bank, "*Seductively* And this might end up being very fun.")
    assert calls[0][0] == "neutral" and bank.asked == ["seductive"]
    calls = _say(FakeBank({"seductive": "x"}), "*Seductively* Hi there you.",
                 emotion_voices=lambda: False)
    assert calls[0][0] == "neutral"


# ---------------------------------------------------------------- variants
class BankVS:
    def __init__(self):
        self.made = []
        self.deleted = []

    def available(self):
        return True

    def synth(self, text, voice, speed=1.0, **o):
        import io

        import numpy as np
        import soundfile as sf
        sr = 16000
        t = np.arange(int(sr * 4)) / sr
        x = 0.3 * np.sin(2 * np.pi * 210 * t).astype("float32")
        b = io.BytesIO()
        sf.write(b, x, sr, format="WAV")
        return b.getvalue()

    def create_clone_profile(self, name, wav, ref_text, language="English"):
        self.made.append((name, ref_text))
        return f"id{len(self.made)}"

    def delete_profile(self, pid):
        self.deleted.append(pid)
        return True


def _bank():
    c = Config(Path(tempfile.mkdtemp()) / "settings.json")
    c.settings.save_vs_profiles = True
    vs = BankVS()
    ready = threading.Event()
    return c, vs, VoiceBank(c, vs, on_ready=lambda k: "#" in k and ready.set()), ready


def test_variant_is_made_in_the_background_from_the_same_voice():
    c, vs, bank, ready = _bank()
    design = voices.effective({"preset": "f-natasha"}, presets.by_id(c.settings.presets))
    assert bank.variant(design, "seductive") == ""          # not yet: never blocks
    assert ready.wait(5)
    vid = bank.variant(design, "seductive")
    assert vid and vid != bank.cached(design)
    assert vs.made[0][1].startswith("Mmm, hello there")     # base clone: the sultry vibe
    assert vs.made[1][1] == MOOD_SCRIPTS["seductive"][0]
    assert bank.variant(design, "nope") == ""


def test_variants_go_with_their_voice():
    c, vs, bank, ready = _bank()
    design = voices.effective({"preset": "f-natasha"}, presets.by_id(c.settings.presets))
    bank.variant(design, "sad")
    assert ready.wait(5)
    key = voices.voice_key(design)
    assert f"{key}#sad" in c.settings.vs_voice_bank
    assert bank.prune(c.used_voice_keys()) == []           # still used: kept
    bank.forget(design)
    assert not any(k.startswith(key) for k in c.settings.vs_voice_bank)
    c.settings.vs_voice_bank = {"old|1": {"id": "a"}, "old|1#sad": {"id": "b"}}
    assert sorted(bank.prune(c.used_voice_keys())) == ["a", "b"]


# ---------------------------------------------------------------- migration
def test_old_settings_get_the_new_cast():
    d = Path(tempfile.mkdtemp())
    (d / "settings.json").write_text(json.dumps({
        "presets": [{"id": "f-s-natasha", "name": "Natasha", "gender": "female", "seed": 9},
                    {"id": "u-mine", "name": "Mine", "gender": "female", "seed": 7}],
        "vs_voice_bank": {"female, low pitch|9": {"id": "oldclone"}},
        "voices": {"G": {
            "Natasha": {"gender": "female", "gender_auto": False, "preset": "f-s-natasha",
                        "voice_override": "", "lines": 5, "aliases": ["Nat"]},
            "Bob": {"gender": "male", "preset": "m-james", "voice_override": "vs:oldclone"},
            "Amy": {"gender": "female", "preset": "u-mine", "voice_override": "sapi:Zira"},
        }}}), encoding="utf-8")
    c = Config(d / "settings.json")
    ids = {p["id"] for p in presets.defaults()}
    assert {p["id"] for p in c.settings.presets} == ids
    ch = c.characters("G")
    assert all(r["preset"] in ids for r in ch.values())
    assert ch["Natasha"]["gender_auto"] is False and ch["Natasha"]["lines"] == 5
    assert ch["Natasha"]["aliases"] == ["Nat"]
    assert ch["Bob"]["voice_override"] == ""               # pointed at a deleted old clone
    assert ch["Amy"]["voice_override"] == "sapi:Zira"      # the user's own pick stays
    assert c.settings.presets_version == presets.PRESETS_VERSION
    saved = json.loads((d / "settings.json").read_text(encoding="utf-8"))
    assert saved["presets_version"] == presets.PRESETS_VERSION
    vs = BankVS()
    assert VoiceBank(c, vs).prune(c.used_voice_keys()) == ["oldclone"]
    time.sleep(0.1)
    assert vs.deleted == ["oldclone"]


def test_new_install_is_not_migrated_again():
    d = Path(tempfile.mkdtemp())
    c = Config(d / "settings.json")
    c.settings.presets.append(presets.new_preset("female"))
    c.save()
    assert len(Config(d / "settings.json").settings.presets) == len(presets.defaults()) + 1


# ------------------------------------------------------------ auto-detect areas
def test_auto_detected_areas_split_a_styled_name_line():
    from app.sources.screenshot_reader import ShotResult, areas_from_result
    res = ShotResult(["NATASHA", "*Seductively* And this might end up being very fun."],
                     [(51.0, 690.0, 60.0, 400.0), (47.0, 790.0, 130.0, 1450.0)],
                     {"name": None, "styled": True})
    name, text = areas_from_result(res, (1920, 1080))
    assert name and text
    assert name[1] + name[3] <= text[1]                      # no overlap
    assert name[0] <= 60 and name[0] + name[2] >= 460         # the whole name fits
    assert text[0] <= 130 and text[0] + text[2] >= 1580       # the whole line fits
    assert text[1] + text[3] <= 1080 and text[3] >= 3 * 47    # room for 3 lines
    res.info["styled"] = False                                # same style: one block
    name, text = areas_from_result(res, (1920, 1080))
    assert name is None and text[1] < 690
    assert areas_from_result(ShotResult(), (100, 100)) == (None, None)


# ------------------------------------------------------------- no stray whispers
def test_only_real_whispers_use_the_whisper_voice():
    for line in ("*softly* Hi there.", "*blushes* Hi there.", "I-I… don't… know…",
                 "(thinking about it…)", "*nervously* Okay.", "*quietly* Okay.",
                 "*sleepily* Morning.", "*scared* What was that?", "*smiles softly* Hi."):
        assert p(line).variant != "whisper", line
    assert p("*softly* Hi there.").mood == "soft"
    assert p("*smiles softly* Hi.").mood == "happy"
    for line in ("*whispers* Come here.", "*whispering* Come here.",
                 "*murmurs into your ear* Come here."):
        assert p(line).variant == "whisper", line


def test_one_ahh_is_not_a_moan():
    assert p("Ahh! A bug!").mood != "aroused"
    assert p("Oh god, I'm late.").mood != "aroused"
    assert p("Ahn… nngh…").mood == "aroused"
    assert p("Ahh… ohh… ahh…").mood == "aroused"


def test_emotion_strength():
    base = p("*Seductively* Hi there you.")
    subtle = p("*Seductively* Hi there you.", strength="subtle")
    strong = p("*Seductively* Hi there you.", strength="strong")
    assert subtle.variant == "" and base.variant == strong.variant == "seductive"
    assert abs((1 - subtle.speed) - (1 - base.speed) / 2) < 0.01
    assert strong.speed < base.speed and strong.speed >= 0.7
    assert p("*angrily* Out!", strength="strong").speed <= 1.3


def test_aroused_variant_is_remade_and_old_one_pruned():
    from app.voice_bank import MOOD_KEYS
    assert MOOD_KEYS["aroused"] == "aroused2"
    c, vs, bank, ready = _bank()
    design = voices.effective({"preset": "f-natasha"}, presets.by_id(c.settings.presets))
    key = voices.voice_key(design)
    assert bank.ensure_variant(design, "aroused")             # made right away
    assert f"{key}#aroused2" in c.settings.vs_voice_bank
    c.settings.vs_voice_bank[f"{key}#aroused"] = {"id": "old-aroused"}
    assert bank.prune(c.used_voice_keys()) == ["old-aroused"]
    assert f"{key}#aroused2" in c.settings.vs_voice_bank


def test_whisper_presets_are_picked_last():
    ps = presets.defaults()
    chars: dict = {}
    whisper = {x["id"] for x in ps if x.get("style") == "whisper"}
    n = sum(x["gender"] == "female" and x["category"] != "narrator" and x["id"] not in whisper
            for x in ps)
    for i in range(n):
        chars[f"g{i}"] = voices.complete_character(f"g{i}", {"gender": "female"}, chars, ps)
    assert not any(c["preset"] in whisper for c in chars.values())
    nxt = voices.complete_character("next", {"gender": "female"}, chars, ps)
    assert nxt["preset"] in whisper                           # only when the rest are taken


def test_characters_on_whisper_presets_get_moved_once():
    d = Path(tempfile.mkdtemp())
    c = Config(d / "settings.json")
    c.settings.voices = {"G": {"Lewd": {"gender": "female", "preset": "f-yumi",
                                        "voice_override": ""}}}
    c.settings.whisper_fix = 0
    c.save()
    raw = json.loads((d / "settings.json").read_text(encoding="utf-8"))
    raw.pop("whisper_fix")                                     # like an older settings file
    (d / "settings.json").write_text(json.dumps(raw), encoding="utf-8")
    c = Config(d / "settings.json")
    assert c.characters("G")["Lewd"]["preset"] not in ("f-yumi", "f-raven")
    c.update_character("G", "Lewd", preset="f-yumi")           # the user picks Yumi again
    assert Config(d / "settings.json").characters("G")["Lewd"]["preset"] == "f-yumi"


# ---------------------------------------------------------------- UI extras
def _qt():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_history_shows_the_mood():
    app = _qt()
    from app.ui.history_view import HistoryView
    h = HistoryView()
    h.install_toolbar(type("H", (), {"add": lambda self, *a: None})())
    h.add("Natasha", "*Seductively* Hi.", "seductive")
    h.add("Ryan", "Hello.")
    assert h.table.columnCount() == 4
    assert h.table.item(0, 3).text() == "seductive" and h.table.item(1, 3).text() == ""
    assert h._text(0).endswith("(seductive)")
    h._filter("seductive")
    assert not h.table.isRowHidden(0) and h.table.isRowHidden(1)
    assert app


def test_cast_card_moods_button():
    app = _qt()
    from app.ui.characters_view import CharacterCard
    ps = presets.by_id(presets.defaults())
    rec = {"gender": "female", "preset": "f-natasha", "voice_override": "", "lines": 1}
    card = CharacterCard("Natasha", rec, [("♀  Natasha", "preset:f-natasha")])
    card.update_rec(rec, voices.effective(rec, ps))
    got = []
    card.preview_moods.connect(got.append)
    card.btn_moods.click()
    assert got == ["Natasha"]
    card.update_rec(dict(rec, voice_override="sapi:Zira"), {})
    assert not card.btn_moods.isEnabled()
    assert app


def test_mood_demo_lines_cover_every_variant():
    from app.ui.main_window import MainWindow
    moods = [m for _p, m in MainWindow.MOOD_DEMO]
    assert set(moods) - {"normal"} == set(MOOD_SCRIPTS)
    for prefix, mood in MainWindow.MOOD_DEMO[1:]:
        d = emotion.parse(prefix + MainWindow.MOOD_SAMPLE)
        assert d.variant == mood, (prefix, d.variant)
