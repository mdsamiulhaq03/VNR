import threading
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app import text_fixes
from app.sources import preprocess
from app.sources.capture import frac_to_rect, rect_to_frac
from app.speech_queue import Line, SpeechQueue
from app.text_clean import clean, split_sentences
from app.tts.base import TTSEngine


# ---------------------------------------------------------------- text
def test_clean():
    assert clean("{i}Hello{/i} there") == "Hello there"
    assert clean("Wait.....  what?") == "Wait… what?"
    assert clean("......") == ""
    assert clean("  a \n b ") == "a b"


def test_split_sentences():
    assert split_sentences("Hello there, friend. How are you doing today?") == \
        ["Hello there, friend.", "How are you doing today?"]
    # short pieces get merged so speech isn't choppy
    assert split_sentences("Oh! Mr. Smith is here already.") == ["Oh! Mr. Smith is here already."]
    assert split_sentences("今日はいい天気ですね。散歩に行きましょうか？") == \
        ["今日はいい天気ですね。", "散歩に行きましょうか？"]
    assert split_sentences("No punctuation at all") == ["No punctuation at all"]


def test_fixups():
    rules = [{"find": "Sayorl", "replace": "Sayori", "whole_word": True},
             {"find": "lt", "replace": "It", "whole_word": True, "case_sensitive": True},
             {"find": "Natsuki", "replace": "Nat-soo-kee", "whole_word": True}]
    assert text_fixes.apply("lt's Sayorl and natsuki!", rules) == "It's Sayori and Nat-soo-kee!"
    assert text_fixes.apply("salt", rules) == "salt"          # whole word only


def test_match_speaker():
    known = ["Sayori", "Monika"]
    assert text_fixes.match_speaker("Sayorl", known) == "Sayori"
    assert text_fixes.match_speaker("monika", known) == "Monika"
    assert text_fixes.match_speaker("Yuri", known) == "Yuri"


def test_frac_roundtrip():
    client = (100, 50, 1280, 720)
    rect = [228, 590, 1024, 160]
    assert frac_to_rect(rect_to_frac(rect, client), client) == rect


# ----------------------------------------------------------- preprocess
def _noisy_text_image(text="Hello from the busy background"):
    rng = np.random.default_rng(1)
    bg = rng.integers(40, 140, size=(90, 700, 3), dtype=np.uint8)      # busy, mid-dark
    img = Image.fromarray(bg, "RGB")
    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(img).text((15, 25), text, fill=(255, 255, 255), font=font)
    return img


def test_color_mask_isolates_text():
    img = _noisy_text_image()
    mask = preprocess.text_mask(img, "color", [255, 255, 255], 60)
    assert 0.01 < mask.mean() < 0.3           # only the letters survive
    auto = preprocess.text_mask(img, "auto")
    assert auto.mean() < 0.5


def test_signature_ignores_background_motion():
    a = _noisy_text_image()
    rng = np.random.default_rng(7)
    arr = np.asarray(a).copy()
    noise = rng.integers(40, 140, size=arr.shape, dtype=np.uint8)
    keep = preprocess.text_mask(a, "color", [255, 255, 255], 60)   # the letters
    arr[~keep] = noise[~keep]                 # same text, different background
    b = Image.fromarray(arr, "RGB")
    sa = preprocess.signature(a, "color", [255, 255, 255], 60)
    sb = preprocess.signature(b, "color", [255, 255, 255], 60)
    assert float(np.abs(sa - sb).mean()) < 3.0


# --------------------------------------------------------------- speech
class FakeEngine(TTSEngine):
    name = "fake"

    def __init__(self, delay=0.2):
        self.delay = delay
        self.calls = []

    def synth(self, text, voice, speed=1.0, **opts):
        self.opts = (voice, opts)
        self.calls.append((time.monotonic(), text))
        time.sleep(self.delay)
        return text.encode()


class FakePlayer:
    def __init__(self):
        self.played = []
        self.times = []
        self.ev = threading.Event()

    def enqueue_bytes(self, b):
        self.played.append(b.decode())
        self.times.append(time.monotonic())
        self.ev.set()

    def stop(self):
        pass


def test_newer_line_wins():
    eng, pl = FakeEngine(), FakePlayer()
    q = SpeechQueue(eng, None, pl)
    q.say(Line("g", "a", "first line here"))
    time.sleep(0.05)                       # first is mid-synthesis
    q.say(Line("g", "a", "second line here"))
    assert pl.ev.wait(2)
    time.sleep(0.4)
    assert pl.played == ["second line here"]
    q.shutdown()


def test_first_sentence_plays_before_rest_is_synthesized():
    eng, pl = FakeEngine(0.3), FakePlayer()
    q = SpeechQueue(eng, None, pl)
    q.say(Line("g", "a", "This is the first sentence. And this is the second one. "
                         "Finally a third one here."))
    deadline = time.time() + 3
    while len(pl.played) < 3 and time.time() < deadline:
        time.sleep(0.02)
    assert len(pl.played) == 3
    last_synth_start = eng.calls[-1][0]
    assert pl.times[0] <= last_synth_start      # sentence 1 queued before 3 began
    q.shutdown()


def test_new_line_cancels_remaining_sentences():
    eng, pl = FakeEngine(0.2), FakePlayer()
    q = SpeechQueue(eng, None, pl)
    q.say(Line("g", "a", "Sentence number one. Sentence number two. Sentence three."))
    assert pl.ev.wait(2)
    q.say(Line("g", "a", "Interrupting line!"))
    time.sleep(0.8)
    assert pl.played[-1] == "Interrupting line!"
    assert "Sentence three." not in pl.played
    q.shutdown()


def test_cache_on_replay():
    eng, pl = FakeEngine(0), FakePlayer()
    q = SpeechQueue(eng, None, pl)
    q.say(Line("g", "a", "hello there"))
    assert pl.ev.wait(2)
    pl.ev.clear()
    q.replay()
    assert pl.ev.wait(2)
    assert len(eng.calls) == 1 and pl.played == ["hello there", "hello there"]
    q.shutdown()
