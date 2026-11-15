"""The live reading loop (OcrSource._run) with a fake screen and a fake OCR."""
import random
import time

import pytest
from PIL import Image, ImageDraw

from app.sources import ocr_source as m
from app.sources.ocr_filter import FilterResult


def _frame(text: str, noise: int = 0) -> Image.Image:
    """Dialogue box: the text drawn over a background; noise > 0 adds moving specks."""
    im = Image.new("RGB", (400, 60), (20, 20, 40))
    d = ImageDraw.Draw(im)
    rnd = random.Random(noise)
    for _ in range(noise):
        x, y = rnd.randrange(400), rnd.randrange(60)
        d.rectangle((x, y, x + 1, y + 1), fill=(200, 60, 60))
    d.text((10, 10), text * 2, fill=(255, 255, 255))
    d.text((10, 30), text[::-1], fill=(255, 255, 255))
    im.info["text"] = text
    return im


class Screen:
    def __init__(self):
        self.text, self.name, self.tick = "", "Mila", 0
        self.animate_name = False
        self.animate_bg = False
        self.typing: list[str] = []      # each capture shows the next, longer part

    def grab(self):
        self.tick += 1
        if self.typing:
            self.text = self.typing.pop(0)
        img = _frame(self.text, noise=(self.tick % 7) * 3 if self.animate_bg else 0)
        shade = (self.tick * 37) % 255 if self.animate_name else 30
        name = Image.new("RGB", (120, 30), (shade, 255 - shade, 90))
        name.info["text"] = self.name
        return img, name


@pytest.fixture()
def loop(monkeypatch):
    screen = Screen()

    class Cap:
        def __init__(self, **_k):
            pass

        def grab_pair(self, _spec):
            return screen.grab()

        def close(self):
            pass

    class Ocr:
        lang = "en-US"

        def read_dialogue(self, img, _opts):
            r = m.Reading()
            r.result = FilterResult()
            t = img.info.get("text", "")
            r.result.lines = [t] if t else []
            r.result.boxes = [(20, 10, 10, 200)] if t else []
            r.result.top_styled = False
            r.raw = [(t, 1.0)] if t else []
            return r

        def read_name(self, img, _opts):
            return img.info.get("text", "")

    monkeypatch.setattr(m, "Capturer", Cap)
    monkeypatch.setattr(m, "make_ocr", lambda _o: Ocr())
    monkeypatch.setattr(m, "dialogue_colour", lambda *a, **k: None)
    spoken = []
    src = m.OcrSource(lambda lines, boxes, info: spoken.append((info["name"], lines[0])),
                      interval=0.02)
    src.spec = m.CaptureSpec("screen", [0, 0, 400, 60], name=[0, 0, 120, 30])
    yield src, screen, spoken
    src.stop()
    time.sleep(0.1)


def _wait(cond, t=3.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_animated_name_box_still_confirms(loop):
    src, screen, spoken = loop
    screen.animate_name = True
    screen.text = "Kharzovya is different though."
    src.start()
    assert _wait(lambda: spoken)
    time.sleep(0.4)
    assert spoken == [("Mila", "Kharzovya is different though.")]


def test_animated_background_confirms_once(loop):
    src, screen, spoken = loop
    screen.animate_bg = True
    screen.animate_name = True
    screen.text = "We can work around it."
    src.start()
    assert _wait(lambda: spoken)
    time.sleep(0.5)
    assert spoken == [("Mila", "We can work around it.")]


def test_typewriter_speaks_only_full_line(loop):
    src, screen, spoken = loop
    full = "Hello there, friend. Nice to see you again."
    screen.typing = [full[:n] for n in range(2, len(full) + 1, 2)] + [full]
    src.start()
    assert _wait(lambda: spoken)
    time.sleep(0.3)
    assert spoken == [("Mila", "Hello there, friend. Nice to see you again.")]


def test_next_line_is_spoken(loop):
    src, screen, spoken = loop
    screen.text = "First line here."
    src.start()
    assert _wait(lambda: len(spoken) == 1)
    screen.text, screen.name = "And a second one.", "AIDA"
    assert _wait(lambda: len(spoken) == 2)
    assert spoken[1] == ("AIDA", "And a second one.")


def test_paused_reads_nothing_and_resume_does_not_repeat(loop):
    src, screen, spoken = loop
    screen.text = "Before the pause."
    src.start()
    assert _wait(lambda: len(spoken) == 1)
    src.paused = True
    screen.text = "Said while away."
    time.sleep(0.3)
    assert len(spoken) == 1
    screen.text = "Before the pause."        # came back to the same line
    src.paused = False
    time.sleep(0.4)
    assert len(spoken) == 1
    screen.text = "A new line."
    assert _wait(lambda: len(spoken) == 2)
