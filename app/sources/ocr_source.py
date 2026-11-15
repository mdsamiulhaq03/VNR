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

from .. import latency
from . import ocr_filter, preprocess
from .capture import Capturer, CaptureSpec, WindowLost


@dataclass
class OcrOptions:
    lang: str = "en-US"
    mode: str = "auto"                 # preprocess mode
    color: list[int] | None = None
    tolerance: int = 60
    ignore_words: list[str] = field(default_factory=list)
    engine: str = "paddle"             # "paddle" (PaddleOCR) | "windows" (Windows OCR)
    gpu: bool = False                  # PaddleOCR on the GPU (DirectML)
    learned_color: list[int] | None = None   # this game's dialogue colour, once learned


def make_ocr(opts: "OcrOptions", limit: int = 960, gpu: bool | None = None):
    """The OCR engine for these options. PaddleOCR reads English only; other languages
    (and a missing PaddleOCR) fall back to Windows OCR."""
    if opts.engine == "paddle" and _is_english(opts.lang):
        from . import paddle_ocr
        if paddle_ocr.available():
            return paddle_ocr.PaddleOcr(opts.lang, opts.gpu if gpu is None else gpu, limit)
    return WindowsOcr(opts.lang)


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
        best.result.top_styled = top_line_styled(img, best.result.boxes)
        return best


    def _name_reads(self, img: Image.Image) -> list[str]:
        reads: list[str] = []
        for variant in self._name_variants(img):
            got = " ".join(t for t, *_ in self.lines_prepared(variant)).strip()
            if got:
                agree = any(r.lower() == got.lower() for r in reads)
                reads.append(got)
                if agree:
                    break                      # two clean-ups agree: good enough
        return reads

    def read_name(self, img: Image.Image, opts: OcrOptions) -> str:
        """The speaker name from a separate name area. '' = no name (narration).

        The name area is small, often a coloured box: read it with several clean-ups
        and let them vote, as for the top line of the reading area."""
        if img is None or img.width < 4 or img.height < 4:
            return ""
        # Read a crop tight around the text first (short names like "Rin" in a big box
        # often read as nothing otherwise), then the whole area.
        tight = _tight_text_crop(img)
        reads = self._name_reads(tight) if tight is not None else []
        if not reads:
            reads = self._name_reads(img)
        if not reads:
            return ""
        name = clean_name_read(self._vote(reads, ""), opts.ignore_words)
        if name and name == name.lower() and not _is_cjk(opts.lang):
            # A lone word gets no context, so Windows OCR often reads "sayori":
            # names are capitalized.
            name = " ".join(w[:1].upper() + w[1:] for w in name.split())
        return name


def _text_colour(img: Image.Image, box: tuple) -> np.ndarray | None:
    """Typical colour of the letters inside a line box: the pixels that differ most from
    the box's median (the background), so it works for light and dark text."""
    h, top, left, w = box
    if not w or not h:
        return None
    x0, y0 = max(0, int(left)), max(0, int(top))
    x1, y1 = min(img.width, int(left + w)), min(img.height, int(top + h))
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    a = np.asarray(img.crop((x0, y0, x1, y1)).convert("RGB"), dtype=np.float32).reshape(-1, 3)
    bg = np.median(a, axis=0)
    d = np.abs(a - bg).sum(axis=1)
    cut = np.percentile(d, 85)
    ink = a[d >= cut]
    return np.median(ink, axis=0) if len(ink) else None


def top_line_styled(img: Image.Image, boxes: list) -> bool | None:
    """Is the top line a different colour or size than the dialogue below it? Name
    lines usually are (blue "LUNA" over white text). None if it can't be told."""
    if len(boxes) < 2 or not all(b and b[3] for b in boxes):
        return None
    # Box height depends on the letters (descenders), so only a clearly bigger top line
    # counts; colour is the main signal.
    rest_h = float(np.median([b[0] for b in boxes[1:]])) or 1.0
    if boxes[0][0] / rest_h >= 1.3:
        return True
    # Each line gets two colour guesses: the pixels least like the background, and the
    # brightest pixels (the fill of outlined text). The top line is "styled" only if
    # none of its colours matches any of the dialogue's. Near-black matches don't count:
    # outlines and shadows are usually shared by the name and the dialogue.
    def colours(b):
        return [c for c in (_text_colour(img, b), _bright_colour(img, b)) if c is not None]

    top = colours(boxes[0])
    rest = [c for b in boxes[1:] for c in colours(b)]
    if not top or not rest:
        return None
    for a in top:
        for c in rest:
            if np.linalg.norm(a - c) <= 60 and max(a.max(), c.max()) > 70:
                return False                 # a shared (non-outline) colour: same style
    return True


def dialogue_colour(img: Image.Image, boxes: list, skip_top: bool) -> list[int] | None:
    """Typical colour of the dialogue letters (for learning a game's text colour).
    Light text -> its fill colour (not the outline); dark text -> the darkest ink."""
    bxs = [b for b in (boxes[1:] if skip_top and len(boxes) > 1 else boxes) if b and b[3]]
    if not bxs:
        return None
    cols = []
    for b in bxs:
        bright, ink = _bright_colour(img, b), _text_colour(img, b)
        h, top, left, w = b
        crop = img.crop((max(0, int(left)), max(0, int(top)),
                         min(img.width, int(left + w)), min(img.height, int(top + h))))
        bg = np.median(np.asarray(crop.convert("RGB"), dtype=np.float32).reshape(-1, 3), axis=0)
        if bright is not None and bright.mean() - bg.mean() > 60:
            cols.append(bright)
        elif ink is not None:
            cols.append(ink)
    return [int(v) for v in np.median(cols, axis=0)] if cols else None


def _bright_colour(img: Image.Image, box: tuple) -> np.ndarray | None:
    """Colour of the brightest ~15% of pixels in a line box (light text fill)."""
    h, top, left, w = box
    x0, y0 = max(0, int(left)), max(0, int(top))
    x1, y1 = min(img.width, int(left + w)), min(img.height, int(top + h))
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    a = np.asarray(img.crop((x0, y0, x1, y1)).convert("RGB"), dtype=np.float32).reshape(-1, 3)
    lum = a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    v = lum + (a.max(axis=1) - a.min(axis=1)) * 0.2      # brightness, a little colour
    ink = a[v >= np.percentile(v, 95)]       # letter fill is often <10% of the box
    return np.median(ink, axis=0) if len(ink) else None


def _tight_text_crop(img: Image.Image, pad: int = 8) -> Image.Image | None:
    """Crop to the text inside a name box: the brightest pixels (light text) or the
    darkest (dark text on a light box), whichever is the smaller, text-like set."""
    g = np.asarray(img.convert("L"), dtype=np.int16)
    best = None
    for m in (g >= int(g.max()) - 40, g <= int(g.min()) + 60):
        n = int(m.sum())
        if 20 <= n <= 0.5 * m.size and (best is None or n < best[0]):
            best = (n, m)
    if best is None:
        return None
    ys, xs = np.where(best[1])
    box = (max(0, xs.min() - pad), max(0, ys.min() - pad),
           min(img.width, xs.max() + pad + 1), min(img.height, ys.max() + pad + 1))
    if box[2] - box[0] < 6 or box[3] - box[1] < 6:
        return None
    return img.crop(box)


def clean_name_read(text: str, ignore_words: list[str] | None = None) -> str:
    """Junk guard for a name-area read: '' when it can't be a name."""
    import re
    s = re.sub(r"^[^\w?]+|[^\w?.)]+$", "", text.strip(), flags=re.UNICODE).strip()
    if not s or not any(ch.isalpha() for ch in s) and s.strip("?"):
        return ""
    if len(s) > 30 or len(s.split()) > 4:
        return ""
    if ignore_words and s.lower() in {w.lower() for w in ignore_words}:
        return ""                              # a menu button, not a name
    letters = sum(ch.isalpha() for ch in s)
    if s.strip("?") and letters < max(1, len(s.replace(" ", "")) // 2):
        return ""                              # mostly symbols: noise
    return s


def _changed(a: np.ndarray | None, b: np.ndarray, thresh: float = 3.0) -> bool:
    return a is None or a.shape != b.shape or float(np.abs(a - b).mean()) > thresh


def _changed_sig(a, b) -> bool:
    if isinstance(b, tuple):
        return not isinstance(a, tuple) or _changed(a[0], b[0]) or _changed(a[1], b[1])
    return isinstance(a, tuple) or _changed(a, b)


def _same_line(a: tuple, b: tuple) -> bool:
    """Same (name, text)? A different speaker is always a new line."""
    return (a[0] or "").lower() == (b[0] or "").lower() and _same(a[1], b[1])


def _still_typing(before: str, now: str) -> bool:
    """The text grew between two reads (a typewriter effect is still writing it out),
    rather than the OCR wobbling by a letter. (A wobble judged as typing only costs one
    more read.)"""
    if len(now) <= len(before):
        return False
    stem = before[:max(0, len(before) - 3)].lower()     # its last letters may be half-drawn
    return now.lower().startswith(stem)


def _same(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


SETTLE = 0.3      # s: read a changed area after this even if it never stops moving


class OcrSource:
    def __init__(self, on_lines: Callable[[list, list, object], None],
                 on_status: Callable[[str], None] = lambda s: None,
                 interval: float = 0.08,
                 on_preview: Callable[[Image.Image, list, object], None] =
                 lambda img, boxes, name_img: None,
                 on_candidate: Callable[[list, list, object], None] =
                 lambda lines, boxes, name: None):
        # on_lines(lines, boxes, name): name is None without a name area, else the
        # name-area read ('' = narration). on_candidate gets the first, unconfirmed read
        # of a frame, so speech can be prepared while the confirming read runs.
        self.on_lines = on_lines
        self.on_status = on_status
        self.on_preview = on_preview
        self.on_candidate = on_candidate
        self._last_preview = 0.0
        self.interval = interval
        self.spec = CaptureSpec()
        self.opts = OcrOptions()
        self._running = False
        self.paused = False        # True: don't look at the screen (game not in front)

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
        def build(o):
            try:
                return make_ocr(o)
            except Exception as e:           # PaddleOCR failed to load: Windows OCR
                self.on_status(f"PaddleOCR unavailable ({e}): using Windows OCR")
                return WindowsOcr(o.lang)
        try:
            ocr = build(self.opts)
        except Exception as e:
            self.on_status(f"OCR unavailable: {e}")
            self._running = False
            return
        built_for = (self.opts.engine, self.opts.lang, self.opts.gpu)
        cap = Capturer(on_note=self.on_status)
        last_sig = None
        stable = 0
        dirty_since = None        # when the frame first changed since the last read
        last_spoken = None        # (name, text) last handed on
        candidate = None          # unconfirmed read, waiting for a second one that agrees
        first = None              # its (lines, boxes, reading)
        done_frame = False        # nothing new to read until the frame changes
        lost = False
        self.on_status("Reading: watching the reading area")
        try:
            while self._running:
                time.sleep(self.interval)
                if self.paused:               # the game isn't in front: keep our place
                    continue
                spec, opts = self.spec, self.opts
                if not spec.text:
                    continue
                if (opts.engine, opts.lang, opts.gpu) != built_for:      # changed in Settings
                    built_for = (opts.engine, opts.lang, opts.gpu)
                    ocr = build(opts)
                    last_sig, candidate, done_frame = None, None, False
                ocr.lang = opts.lang
                try:
                    img, name_img = cap.grab_pair(spec)
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
                if not self._running:                   # stopped while capturing
                    break
                if now - self._last_preview > 0.5:      # live preview, ~2 fps
                    self._last_preview = now
                    try:
                        self.on_preview(img, [], name_img)
                    except RuntimeError:                # the window is closing
                        break
                # Only the dialogue area decides "something changed": name boxes are
                # often see-through, with an animated scene behind them. And a change
                # only means "read again": whether it's a new line is decided by the
                # text (two reads that agree), so a moving background can't hold a
                # line back.
                sig = preprocess.signature(img, opts.mode, opts.color, opts.tolerance)
                if _changed(last_sig, sig):
                    if done_frame or candidate is None:
                        latency.changed()
                    last_sig, stable, done_frame = sig, 0, False
                    dirty_since = dirty_since or now
                else:
                    stable += 1
                if done_frame:
                    continue
                # A fresh change: wait one still frame (typing, fades), but never longer
                # than SETTLE when the area keeps moving.
                if candidate is None and stable < 1 and now - (dirty_since or now) < SETTLE:
                    continue
                dirty_since = None
                latency.mark("ocr" if candidate is None else "confirm-ocr")
                try:
                    reading = ocr.read_dialogue(img, opts)
                    name = ocr.read_name(name_img, opts) if name_img is not None else None
                except Exception as e:
                    self.on_status(f"OCR error: {e}")
                    continue
                self._last_preview = time.monotonic()
                self.on_preview(img, list(reading.result.boxes), name_img)
                text = reading.text
                if not text:
                    done_frame, candidate = True, None
                    last_spoken = None if not reading.raw else last_spoken
                    continue
                said = (name, text)
                if last_spoken and _same_line(said, last_spoken):
                    done_frame, candidate = True, None      # still the line we spoke
                    continue
                if candidate is None or not _same_line(candidate, said) \
                        or _still_typing(candidate[1], text):
                    candidate = said          # wait for a second read that agrees
                    first = (list(reading.lines), list(reading.result.boxes), reading)
                    latency.mark("read")
                    try:                      # ...but start preparing the voice now
                        self.on_candidate(list(reading.lines), list(reading.result.boxes),
                                          {"name": name, "styled": reading.result.top_styled})
                    except Exception:
                        pass
                    continue
                done_frame = True
                latency.mark("confirmed")
                # The reads agree: pass on the FIRST read, the exact text whose voice is
                # already being made (a second read often differs by a letter, which
                # would throw that work away and start over).
                said, (lines, boxes, reading) = candidate, first
                last_spoken, candidate = said, None
                try:                      # for learning this game's dialogue colour
                    colour = dialogue_colour(img, boxes,
                                             skip_top=said[0] is None and len(lines) > 1)
                except Exception:
                    colour = None
                self.on_lines(lines, boxes,
                              {"name": said[0], "styled": reading.result.top_styled,
                               "colour": colour, "name_img": name_img})
        finally:
            cap.close()
