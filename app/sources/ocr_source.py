"""Screen-OCR text source for any visual novel.

Polls the reading area (fixed on screen, or relative to the game window), waits until
the text stops changing, then:
  1. OCRs both the cleaned-up image and the plain image, keeps the cleaner result,
  2. drops menu-button rows, tiny UI text and garbled junk (ocr_filter),
  3. reads it once more a moment later and only speaks if both reads agree
     (kills garbage from fades and scene transitions).
Emits the kept lines, so the speaker name on the top line can be split off later.
"""
from __future__ import annotations

import asyncio
import difflib
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from PIL import Image

from . import ocr_filter, preprocess
from .capture import Capturer, CaptureSpec, WindowLost


@dataclass
class OcrOptions:
    lang: str = "en-US"
    mode: str = "auto"                 # preprocess mode
    color: list[int] | None = None
    tolerance: int = 60
    ignore_words: list[str] = field(default_factory=list)


def _is_cjk(lang: str) -> bool:
    return lang.split("-")[0].lower() in ("ja", "zh", "ko")


def _is_english(lang: str) -> bool:
    return lang.split("-")[0].lower() == "en"


@dataclass
class Reading:
    """Everything one OCR pass found (also shown by Test OCR)."""
    raw: list[tuple[str, float]] = field(default_factory=list)
    used: str = "cleaned"                       # which image won: "cleaned" / "plain"
    result: ocr_filter.FilterResult = field(default_factory=ocr_filter.FilterResult)

    @property
    def lines(self) -> list[str]:
        return self.result.lines

    @property
    def text(self) -> str:
        return " ".join(self.result.lines)


class WindowsOcr:
    def __init__(self, lang: str = "en-US"):
        import winocr  # noqa: F401  (fail early if missing)
        self.lang = lang
        self._loop = asyncio.new_event_loop()

    def lines_prepared(self, img: Image.Image) -> list[tuple]:
        """[(line text, height, top, left, width)] in pixels of the given image."""
        import winocr
        if img.mode != "RGB":
            img = img.convert("RGB")
        res = self._loop.run_until_complete(winocr.recognize_pil(img, self.lang))
        out = []
        for ln in res.lines:
            words = list(ln.words)
            if _is_cjk(self.lang):      # Windows OCR puts spaces between CJK characters
                t = "".join(w.text for w in words)
            else:
                t = ln.text
            try:
                rects = [w.bounding_rect for w in words]
                h = max(float(r.height) for r in rects)
                top = min(float(r.y) for r in rects)
                left = min(float(r.x) for r in rects)
                right = max(float(r.x + r.width) for r in rects)
            except Exception:
                h = top = left = right = 0.0
            if t.strip():
                out.append((t.strip(), h, top, left, right - left))
        return out

    def read_lines(self, img: Image.Image, opts: OcrOptions | None = None) -> list[str]:
        o = opts or OcrOptions(lang=self.lang)
        return [t for t, *_ in self.lines_prepared(
            preprocess.process(img, o.mode, o.color, o.tolerance))]

    def read(self, img: Image.Image, opts: OcrOptions | None = None) -> str:
        sep = "" if _is_cjk(self.lang) else " "
        return sep.join(self.read_lines(img, opts)).strip()

    def read_prepared(self, img: Image.Image) -> str:
        sep = "" if _is_cjk(self.lang) else " "
        return sep.join(t for t, *_ in self.lines_prepared(img)).strip()

    def _reread_extra(self, img: Image.Image, plain: list[tuple], cleaned_txt: list[str]):
        """Lines only the plain read found (e.g. a name on a coloured name box) are often
        misread because of low contrast. Re-read each one from a tight crop, cleaned up
        on its own, where the text separates well from its box."""
        return [item if any(_same(c, item[0]) for c in cleaned_txt)
                else self._reread_line(img, item) for item in plain]

    def _reread_line(self, img: Image.Image, item: tuple) -> tuple:
        """Re-read one line from a tight crop with several clean-ups and a vote."""
        k = 3 if img.height < 150 else 2
        text, h, top, left, width = (list(item) + [0, 0, 0, 0])[:5]
        if not width:
            return item
        reads = [text]                      # the original read gets a vote too
        # Two crops: tight, and wider (a crop edge right on a name box's border confuses
        # the OCR). All clean-ups of both crops vote together.
        for px in (0.6, 1.4):
            pad = h / k * px
            x0 = max(0, int((left - 12) / k - pad))
            y0 = max(0, int((top - 12) / k - h / k * 0.6))
            x1 = min(img.width, int((left - 12 + width) / k + pad))
            y1 = min(img.height, int((top - 12 + h) / k + h / k * 0.6))
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            for variant in self._name_variants(img.crop((x0, y0, x1, y1))):
                got = " ".join(t for t, *_ in self.lines_prepared(variant)).strip()
                if got:
                    reads.append(got)
        return (self._vote(reads, text), *item[1:])

    @staticmethod
    def _vote(reads: list[str], fallback: str) -> str:
        """Several clean-ups of the same strip: the read most of them agree on wins.
        Ties go to real words, then to the read that holds together ("Sayori" > "Sa ori")."""
        if not reads:
            return fallback
        from collections import Counter
        votes = Counter(r.strip() for r in reads)
        return max(votes, key=lambda r: (votes[r], ocr_filter.text_quality(r),
                                         -r.count(" "), len(r)))

    @staticmethod
    def _name_variants(crop: Image.Image) -> list[Image.Image]:
        import numpy as np
        out = [preprocess.process(crop, "off", scale=4),
               preprocess.process(crop, "auto", scale=4)]
        g = np.asarray(crop.convert("L"), dtype=np.int16)
        hi = int(g.max())
        for cut in (hi - 40, hi - 80):        # keep only the brightest pixels (light text)
            m = Image.fromarray(np.where(g >= cut, 0, 255).astype(np.uint8), "L")
            out.append(preprocess.ImageOps.expand(
                m.resize((m.width * 4, m.height * 4), Image.LANCZOS), border=12, fill=255))
        lo = int(g.min())                     # and the darkest (dark text on a light box)
        m = Image.fromarray(np.where(g <= lo + 60, 0, 255).astype(np.uint8), "L")
        out.append(preprocess.ImageOps.expand(
            m.resize((m.width * 4, m.height * 4), Image.LANCZOS), border=12, fill=255))
        return out

    def read_dialogue(self, img: Image.Image, opts: OcrOptions) -> Reading:
        """Best-effort clean dialogue lines from the reading area."""
        cleaned = self.lines_prepared(preprocess.process(img, opts.mode, opts.color,
                                                         opts.tolerance))
        best = Reading(cleaned, "cleaned")
        if opts.mode != "off":
            plain = self.lines_prepared(preprocess.process(img, "off"))
            if _is_english(opts.lang):
                c_txt = [t for t, *_ in cleaned]
                p_txt = [t for t, *_ in plain]
                # The plain read has every cleaned line *plus* more (typically the name on
                # a coloured name box that the clean-up erased): it saw more.
                superset = len(p_txt) > len(c_txt) and all(
                    any(_same(c, p) for p in p_txt) for c in c_txt)
                sc = ocr_filter.text_quality(" ".join(c_txt))
                sp = ocr_filter.text_quality(" ".join(p_txt))
                if superset:
                    plain = self._reread_extra(img, plain, c_txt)
                    best = Reading(plain, "plain")
                elif sp > sc + 0.5:
                    best = Reading(plain, "plain")
            elif not cleaned and plain:
                best = Reading(plain, "plain")
        # A short top line is probably a speaker name, often on a coloured name box:
        # re-read it closely so the name comes out right ("Kenji", not "…enki").
        if len(best.raw) >= 2:
            top = best.raw[0][0]
            if len(top) <= 30 and len(top.split()) <= 4:
                best.raw = [self._reread_line(img, best.raw[0])] + list(best.raw[1:])
        best.result = ocr_filter.filter_lines(best.raw, opts.ignore_words, opts.lang)
        # Boxes are in the upscaled+bordered image (see preprocess.process): convert back
        # to the captured area's pixels.
        k = 3 if img.height < 150 else 2
        best.result.boxes = [(h / k, (t - 12) / k, (l - 12) / k, w / k)
                             for h, t, l, w in best.result.boxes]
        return best


def _changed(a: np.ndarray | None, b: np.ndarray, thresh: float = 3.0) -> bool:
    return a is None or a.shape != b.shape or float(np.abs(a - b).mean()) > thresh


def _same(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


class OcrSource:
    def __init__(self, on_lines: Callable[[list, list], None],
                 on_status: Callable[[str], None] = lambda s: None,
                 interval: float = 0.25,
                 on_preview: Callable[[Image.Image, list], None] = lambda img, boxes: None):
        self.on_lines = on_lines
        self.on_status = on_status
        self.on_preview = on_preview
        self._last_preview = 0.0
        self.interval = interval
        self.spec = CaptureSpec()
        self.opts = OcrOptions()
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        if not self.spec.text:
            self.on_status("Draw the reading area first")
            return
        self._running = True
        threading.Thread(target=self._run, name="ocr", daemon=True).start()

    def stop(self) -> None:
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def _run(self) -> None:
        try:
            ocr = WindowsOcr(self.opts.lang)
        except Exception as e:
            self.on_status(f"OCR unavailable: {e}")
            self._running = False
            return
        cap = Capturer(on_note=self.on_status)
        last_sig = None
        stable = 0
        last_spoken = ""
        candidate = None          # first read of the current (settled) frame
        done_frame = False        # already handled this frame
        lost = False
        self.on_status("Reading: watching the reading area")
        try:
            while self._running:
                time.sleep(self.interval)
                spec, opts = self.spec, self.opts
                if not spec.text:
                    continue
                ocr.lang = opts.lang
                try:
                    img = cap.grab(spec)
                except WindowLost as e:
                    if not lost:
                        self.on_status(f"{e}: waiting for it to come back")
                        lost = True
                    time.sleep(1.0)
                    continue
                except Exception as e:
                    self.on_status(f"Capture failed: {e}")
                    time.sleep(1.0)
                    continue
                if lost:
                    lost = False
                    self.on_status("Game window found again")
                if img is None:               # minimized
                    continue
                now = time.monotonic()
                if now - self._last_preview > 0.5:      # live preview, ~2 fps
                    self._last_preview = now
                    self.on_preview(img, [])
                sig = preprocess.signature(img, opts.mode, opts.color, opts.tolerance)
                if _changed(last_sig, sig):
                    last_sig, stable, candidate, done_frame = sig, 0, None, False
                    continue
                stable += 1
                if stable < 2 or done_frame:
                    continue
                try:
                    reading = ocr.read_dialogue(img, opts)
                except Exception as e:
                    self.on_status(f"OCR error: {e}")
                    continue
                self._last_preview = time.monotonic()
                self.on_preview(img, list(reading.result.boxes))
                text = reading.text
                if not text:
                    done_frame = True
                    last_spoken = "" if not reading.raw else last_spoken
                    continue
                if candidate is None or not _same(candidate, text):
                    candidate = text          # wait for a second read that agrees
                    continue
                done_frame = True
                if _same(text, last_spoken):
                    continue
                last_spoken = text
                self.on_lines(list(reading.lines), list(reading.result.boxes))
        finally:
            cap.close()
