import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from app.config import NARRATOR, Config
from app.sources.capture import Capturer, CaptureSpec
from app.sources.ocr_source import _same_line, clean_name_read
from app.speech_queue import Line, SpeechQueue
from app.tts.base import TTSEngine


def cfg():
    return Config(Path(tempfile.mkdtemp()) / "settings.json")


class FakeVS(TTSEngine):
    name = "vs"

    def __init__(self, delay=0.0):
        self.calls = []
        self.delay = delay
        self.lock = threading.Lock()

    def available(self):
        return True

    def synth(self, text, voice, speed=1.0, **opts):
        with self.lock:
            self.calls.append((text, opts.get("steps")))
        time.sleep(self.delay)
        return b"RIFF" + text.encode()


class FakePlayer:
    paused = False

    def __init__(self):
        self.got = []

    def stop(self):
        pass

    def enqueue_bytes(self, b):
        self.got.append(b)

    def is_playing(self):
        return False


def wait_for(cond, t=5.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


LINE = Line("G", "Aiko", "Ahaha, you should see your face. Don't worry, I will help you.",
            {}, "en", {"gender": "female", "seed": 1, "label": "Hana"})


# ------------------------------------------------------------- quick start
def test_quick_start_first_sentence_fast():
    vs, pl = FakeVS(), FakePlayer()
    q = SpeechQueue(vs, None, pl)
    q.quick_steps = lambda: 8
    q.say(LINE)
    assert wait_for(lambda: len(pl.got) == 2)
    assert [s for _, s in vs.calls] == [8, None]
    q.shutdown()


def test_quick_start_off():
    vs, pl = FakeVS(), FakePlayer()
    q = SpeechQueue(vs, None, pl)
    q.say(LINE)
    assert wait_for(lambda: len(pl.got) == 2)
    assert [s for _, s in vs.calls] == [None, None]
    q.shutdown()


# ---------------------------------------------------------------- prefetch
def test_prefetch_then_say_makes_one_request():
    vs, pl = FakeVS(delay=0.3), FakePlayer()
    q = SpeechQueue(vs, None, pl)
    q.quick_steps = lambda: 8
    q.prefetch(LINE)
    time.sleep(0.05)                    # prefetch is still running
    q.say(LINE)
    assert wait_for(lambda: len(pl.got) == 2)
    firsts = [c for c in vs.calls if c[1] == 8]
    assert len(firsts) == 1             # say() waited for the prefetch instead of re-asking
    q.shutdown()


def test_prefetch_plays_nothing():
    vs, pl = FakeVS(), FakePlayer()
    q = SpeechQueue(vs, None, pl)
    q.prefetch(LINE)
    assert wait_for(lambda: len(vs.calls) == 1)
    time.sleep(0.1)
    assert pl.got == []
    q.shutdown()


# --------------------------------------------------------------- name area
def interpret(c, lines, name):
    from app.ui.main_window import MainWindow
    me = SimpleNamespace(cfg=c, game="G")
    return MainWindow._interpret(me, lines, None, name)


def test_name_area_skips_top_line_split():
    c = cfg()
    # Short capitalized first line: without a name area this looked like a name.
    sp, text, _ = interpret(c, ["Good Morning", "Did you sleep well?"], "Aiko")
    assert sp == "Aiko" and text.startswith("Good Morning")


def test_name_area_empty_is_narrator():
    c = cfg()
    sp, text, _ = interpret(c, ["The rain kept falling."], "")
    assert sp == NARRATOR and text == "The rain kept falling."


def test_name_area_misread_matches_known():
    c = cfg()
    c.character("G", "Sayori")
    sp, _, _ = interpret(c, ["Hi there!"], "Sayorl")
    assert sp == "Sayori"


def test_name_read_junk_guard():
    assert clean_name_read("|·") == ""
    assert clean_name_read("  Aiko ") == "Aiko"
    assert clean_name_read("???") == "???"
    assert clean_name_read("Mr. Tanaka") == "Mr. Tanaka"
    assert clean_name_read("This is a whole sentence of dialogue, not a name") == ""
    assert clean_name_read("Skip", ["Skip", "Auto"]) == ""


def test_same_line_new_speaker_is_new_line():
    assert _same_line(("Aiko", "Hello there."), ("aiko", "Hello there."))
    assert not _same_line(("Aiko", "Hello there."), ("Ren", "Hello there."))
    assert not _same_line((None, "Hello there."), ("Ren", "Hello there."))


def test_grab_pair_one_frame():
    full = Image.new("RGB", (400, 300), (0, 0, 0))
    full.paste((255, 0, 0), (10, 10, 110, 40))        # name area: red
    full.paste((0, 0, 255), (10, 200, 390, 290))      # text area: blue
    cap = Capturer.__new__(Capturer)
    shots = []

    def grab_screen(rect):
        shots.append(rect)
        l, t, w, h = rect
        return full.crop((l, t, l + w, t + h))
    cap._grab_screen = grab_screen
    text, name = cap.grab_pair(CaptureSpec("screen", [10, 200, 380, 90], name=[10, 10, 100, 30]))
    assert len(shots) == 1                            # one grab for both areas
    assert name.size == (100, 30) and name.getpixel((5, 5)) == (255, 0, 0)
    assert text.size == (380, 90) and text.getpixel((5, 5)) == (0, 0, 255)
    text, name = cap.grab_pair(CaptureSpec("screen", [10, 200, 380, 90]))
    assert name is None and text.size == (380, 90)


# ------------------------------------------------------------ v6.1 additions
import json as _json

from app.name_split import split_name
from app.sources.ocr_filter import correct, repair_ocr


def test_repairs_contractions_and_at_sign():
    fixed = correct("rm sure I'll be fine. ltd say itrs okay, rm here.", [])[0]
    assert fixed == "I'm sure I'll be fine. I'd say it's okay, I'm here."
    assert correct("I tm sure I 'II be fine.", [])[0] == "I'm sure I'll be fine."
    assert correct("ye@h... be@r with us, once'in @ life", [])[0] == \
        "yeah... bear with us, once in a life"
    assert correct("11 am so tired.", [])[0] == "I'm so tired."
    assert repair_ocr("I don 't know, dont worry.")[0] == "I don't know, don't worry."


def test_repairs_leave_good_text_alone():
    for s in ["Meet me at 11 am tomorrow.", "Acme Ltd. is here.", "I feel ill today.",
              "Nngh... Senpai, haaah, fuck! Hehe.", "Sooooo yeah, I'm fine.",
              "this is a once in a lifet-"]:
        assert correct(s, [])[0] == s, s


def test_caps_or_coloured_top_line_is_a_name():
    sp, text = split_name(["LUNA", "It felt like what I saw was really close."], [])
    assert sp == "LUNA" and text.startswith("It felt")
    sp, _ = split_name(["Good Morning", "Did you sleep well?"], [], styled=True)
    assert sp == "Good Morning"
    sp, _ = split_name(["The rain kept falling", "and nobody came."], [],
                       boxes=[(30, 0, 10, 300), (30, 40, 10, 300)], styled=False)
    assert sp == ""


def test_rename_keeps_voice_and_old_name_still_matches():
    c = cfg()
    rec = c.character("G", "PRESENTER")
    preset = rec["preset"]
    assert c.rename_character("G", "PRESENTER", "Presenter") == "Presenter"
    chars = c.characters("G")
    assert "PRESENTER" not in chars and chars["Presenter"]["preset"] == preset
    sp, text, _ = interpret(c, ["PRESENTER", "Welcome back, everyone!"], None)
    assert sp == "Presenter" and text == "Welcome back, everyone!"
    sp, _, _ = interpret(c, ["Hello there."], "PRESENTER")         # name area too
    assert sp == "Presenter"
    assert c.game_fixups("G") == []          # dialogue text is never rewritten


def test_rename_onto_existing_merges():
    c = cfg()
    c.count_line("G", "Luna")
    c.count_line("G", "LUNA")
    assert c.rename_character("G", "LUNA", "Luna") == "Luna"
    assert "LUNA" not in c.characters("G") and c.characters("G")["Luna"]["lines"] == 2


def test_v2_voice_set_replaces_old_presets_once():
    from app import presets
    d = Path(tempfile.mkdtemp())
    old = [{"id": "f-s-natasha", "name": "Natasha", "gender": "female", "seed": 1409},
           {"id": "u-mine", "name": "Mine", "gender": "female", "seed": 7}]
    (d / "settings.json").write_text(_json.dumps({"presets": old}), encoding="utf-8")
    c = Config(d / "settings.json")
    ids = [p["id"] for p in c.settings.presets]
    assert ids == [p["id"] for p in presets.defaults()]          # everything replaced
    c.settings.presets = [p for p in c.settings.presets if p["id"] != "f-ruby"]
    c.save()
    ids = [p["id"] for p in Config(d / "settings.json").settings.presets]
    assert "f-ruby" not in ids                  # a deleted new preset stays deleted


def test_paddle_reads_outlined_game_text():
    from PIL import ImageDraw, ImageFont
    from app.sources import paddle_ocr
    from app.sources.ocr_source import OcrOptions, make_ocr
    if not paddle_ocr.available():
        return
    f = ImageFont.truetype("arial.ttf", 30)
    img = Image.new("RGB", (900, 130), (200, 190, 160))
    d = ImageDraw.Draw(img)
    d.text((20, 10), "PRESENTER", fill=(250, 190, 20), font=f, stroke_width=3,
           stroke_fill="black")
    d.text((40, 60), "Sooooo yeah, bear with us. I'm sure it's fine.", fill="white", font=f,
           stroke_width=3, stroke_fill="black")
    o = OcrOptions(engine="paddle")
    ocr = make_ocr(o)
    assert type(ocr).__name__ == "PaddleOcr"
    r = ocr.read_dialogue(img, o)
    assert r.lines[0] == "PRESENTER"
    assert "bear with us" in r.lines[1] and "I'm sure" in r.lines[1]
    assert r.result.top_styled is True
