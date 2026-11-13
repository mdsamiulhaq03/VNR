import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.config import DEFAULT_IGNORE_WORDS, Config
from app.sources import paddle_ocr
from app.sources.ocr_filter import correct, repair_ocr, unknown_words
from app.sources.ocr_source import OcrOptions, dialogue_colour, make_ocr
from app.sources.screenshot_reader import find_dialogue


def cfg():
    return Config(Path(tempfile.mkdtemp()) / "settings.json")


def scene(name="Kaelith", lines=("Welcome to Eldoria! Did you sleep well?",
                                 "I made breakfast for both of us.")):
    rng = np.random.default_rng(5)
    img = Image.fromarray(rng.integers(40, 200, (1080, 1920, 3)).astype(np.uint8))
    d = ImageDraw.Draw(img)
    d.text((40, 30), "Save   Load   Auto   Skip   Menu", fill="white",
           font=ImageFont.truetype("arial.ttf", 24))
    d.rounded_rectangle((120, 790, 1800, 1040), 20, fill=(20, 20, 40))
    if name:
        d.rounded_rectangle((140, 720, 420, 775), 12, fill=(230, 120, 170))
        d.text((165, 728), name, fill="white", font=ImageFont.truetype("arialbd.ttf", 34))
    for i, ln in enumerate(lines):
        d.text((170, 830 + 60 * i), ln, fill="white", font=ImageFont.truetype("georgia.ttf", 38))
    return img


# ------------------------------------------------------------- screenshots
def test_screenshot_auto_detect_finds_name_and_dialogue():
    if not paddle_ocr.available():
        return
    o = OcrOptions(ignore_words=list(DEFAULT_IGNORE_WORDS))
    r = find_dialogue(scene(), make_ocr(o), make_ocr(o, limit=1920), o)
    assert r.method == "auto-detected"
    assert r.info.get("name") == "Kaelith"
    text = " ".join(r.lines)
    assert "Welcome to Eldoria" in text and "breakfast" in text
    assert "Load" not in text                      # the menu bar is not dialogue


def test_screenshot_uses_saved_areas_when_shape_matches():
    if not paddle_ocr.available():
        return
    o = OcrOptions(ignore_words=list(DEFAULT_IGNORE_WORDS))
    areas = {"text": [150 / 1920, 810 / 1080, 1600 / 1920, 220 / 1080],
             "name": [140 / 1920, 715 / 1080, 290 / 1920, 65 / 1080]}
    r = find_dialogue(scene(), make_ocr(o), make_ocr(o, limit=1920), o, areas, 16 / 9)
    assert r.method == "saved areas" and r.info["name"] == "Kaelith"
    # a different shape (e.g. a cropped screenshot) falls back to auto-detection
    r = find_dialogue(scene(), make_ocr(o), make_ocr(o, limit=1920), o, areas, 4 / 3)
    assert r.method == "auto-detected"


# ------------------------------------------------------------- word memory
def test_words_learned_after_three_lines_and_protected():
    c = cfg()
    for _ in range(2):
        assert c.learn_words("G", ["Eldoria"]) == []
    assert c.learn_words("G", ["Eldoria"]) == ["Eldoria"]
    assert "Eldoria" in c.game_words("G")
    c.remove_game_word("G", "Eldoria")
    for _ in range(5):
        c.learn_words("G", ["Eldoria"])
    assert "Eldoria" not in c.game_words("G")           # removed words stay removed
    c.add_game_word("G", "Kaelith")
    assert c.word_entries("G") == [("Kaelith", False)]


def test_game_words_never_corrected():
    assert correct("Welcome to Eldorra, Kaelth.", [], ["Eldorra", "Kaelth"])[0] == \
        "Welcome to Eldorra, Kaelth."
    assert unknown_words("Welcome to Eldoria, Hmmm, ahaha, Senpai!") == ["Eldoria"]


def test_spacing_repairs():
    assert repair_ocr("Hehe,you reallythought that would work?")[0] == \
        "Hehe, you really thought that would work?"
    assert repair_ocr("It costs 1,000 yen.")[0] == "It costs 1,000 yen."
    assert repair_ocr("Ahahahaha, Sooooo good.")[0] == "Ahahahaha, Sooooo good."


# ---------------------------------------------------------- learned colour
def test_text_colour_learned_after_five_agreeing_lines():
    c = cfg()
    for i in range(4):
        assert not c.record_text_colour("G", [250, 250, 245 + i])
    assert c.record_text_colour("G", [251, 250, 248])
    assert c.learned_text_colour("G")[0] >= 245
    c2 = cfg()
    for col in ([250, 250, 250], [20, 20, 20], [250, 250, 250], [20, 20, 20], [250, 0, 0]):
        c2.record_text_colour("G", col)
    assert c2.learned_text_colour("G") is None          # inconsistent: nothing learned


def test_dialogue_colour_is_the_fill_not_the_outline():
    img = Image.new("RGB", (900, 120), (90, 140, 200))
    d = ImageDraw.Draw(img)
    d.text((20, 40), "Outlined white dialogue text here", fill=(255, 255, 255),
           font=ImageFont.truetype("arial.ttf", 36), stroke_width=3, stroke_fill=(0, 0, 0))
    col = dialogue_colour(img, [(50, 35, 15, 700)], skip_top=False)
    assert min(col) > 200


def test_learned_colour_rescues_a_busy_background():
    if not paddle_ocr.available():
        return
    rng = np.random.default_rng(2)
    img = Image.fromarray(rng.integers(0, 255, (110, 1000, 3)).astype(np.uint8))
    d = ImageDraw.Draw(img)
    d.text((20, 30), "The festival lights were beautiful tonight.", fill=(255, 230, 90),
           font=ImageFont.truetype("arialbd.ttf", 34))
    o = OcrOptions(learned_color=[255, 230, 90])
    r = make_ocr(o).read_dialogue(img, o)
    assert "festival lights were beautiful" in " ".join(r.lines)


def test_gpu_setting_falls_back_cleanly():
    if not paddle_ocr.available():
        return
    o = OcrOptions(gpu=True)
    ocr = make_ocr(o)                                   # GPU if possible, else CPU
    img = Image.new("RGB", (600, 80), (20, 20, 30))
    ImageDraw.Draw(img).text((10, 20), "Hello there friend", fill="white",
                             font=ImageFont.truetype("arial.ttf", 32))
    assert ocr.read_dialogue(img, o).lines == ["Hello there friend"]
