"""PaddleOCR engine (PP-OCRv4 models, run with ONNX Runtime through RapidOCR).

Much better than Windows OCR on game fonts: outlined, rounded and coloured text on
busy backgrounds ("Sooooo yeah... bear with us" instead of "ye@h... be@r"). English
only here (the English recognition model); other languages use Windows OCR.

Same interface as WindowsOcr: read_dialogue(img, opts) -> Reading, read_name(img, opts).
"""
from __future__ import annotations

import logging
import sys
import threading
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image

from . import ocr_filter
from .ocr_source import Reading, _is_english, clean_name_read, top_line_styled

_engines: dict[tuple, object] = {}
_lock = threading.Lock()
_gpu_failed = False
MIN_SCORE = 0.5          # lines the model itself isn't sure about are noise
# PP-OCRv6 small: measured best on game text (37/38 exact vs 30/38 for PP-OCRv4 mobile
# English) and the fastest on the CPU (~57 ms per dialogue box).
DET_MODEL, REC_MODEL = "PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx"


def _models_dir() -> Path | None:
    bases = []
    if getattr(sys, "_MEIPASS", None):
        bases.append(Path(sys._MEIPASS) / "rapidocr" / "models")
    try:
        import rapidocr
        bases.append(Path(rapidocr.__file__).parent / "models")
    except Exception:
        pass
    for b in bases:
        if (b / REC_MODEL).exists() and (b / DET_MODEL).exists():
            return b
    return None


def gpu_available() -> bool:
    try:
        import onnxruntime
        return "DmlExecutionProvider" in onnxruntime.get_available_providers()
    except Exception:
        return False


def available() -> bool:
    try:
        import onnxruntime  # noqa: F401
        import rapidocr  # noqa: F401
        return True
    except Exception:
        return False


def engine(gpu: bool = False, limit: int = 960):
    """A shared RapidOCR engine per (gpu, detection size). Dialogue boxes use 960 px;
    whole screenshots use a bigger limit so small text isn't lost. GPU (DirectML) falls
    back to the CPU if it can't start."""
    global _gpu_failed
    gpu = gpu and not _gpu_failed and gpu_available()
    key = (gpu, limit)
    with _lock:
        if key not in _engines:
            from rapidocr import ModelType, OCRVersion, RapidOCR
            for name in ("RapidOCR", "rapidocr"):
                logging.getLogger(name).setLevel(logging.WARNING)
            params = {
                "Global.use_cls": False,
                "Global.log_level": "warning",
                "Det.ocr_version": OCRVersion.PPOCRV6, "Det.model_type": ModelType.SMALL,
                "Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.model_type": ModelType.SMALL,
                # Dialogue boxes are wide and short: limit the long side instead of
                # blowing the short side up (10x faster, same result).
                "Det.limit_type": "max", "Det.limit_side_len": limit,
            }
            if gpu:
                params["EngineConfig.onnxruntime.use_dml"] = True
            d = _models_dir()
            if d:
                params["Det.model_path"] = str(d / DET_MODEL)
                params["Rec.model_path"] = str(d / REC_MODEL)
            try:
                try:
                    eng = RapidOCR(params=params)
                except TypeError:
                    params.pop("Global.log_level", None)
                    eng = RapidOCR(params=params)
                if gpu:                              # make sure the GPU really works
                    eng(np.zeros((32, 96, 3), np.uint8))
            except Exception:
                if not gpu:
                    raise
                _gpu_failed = True
                return _engines.get((False, limit)) or _cpu_engine(limit)
            _engines[key] = eng
        return _engines[key]


def _cpu_engine(limit: int):
    """CPU engine while already holding the lock (GPU start-up failed)."""
    from rapidocr import ModelType, OCRVersion, RapidOCR
    params = {"Global.use_cls": False, "Det.limit_type": "max", "Det.limit_side_len": limit,
              "Det.ocr_version": OCRVersion.PPOCRV6, "Det.model_type": ModelType.SMALL,
              "Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.model_type": ModelType.SMALL}
    d = _models_dir()
    if d:
        params["Det.model_path"] = str(d / DET_MODEL)
        params["Rec.model_path"] = str(d / REC_MODEL)
    eng = RapidOCR(params=params)
    _engines[(False, limit)] = eng
    return eng


def gpu_failed() -> bool:
    return _gpu_failed


def _plain(s: str) -> str:
    """'cloše' -> 'close': the model's character set has accents English text doesn't."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


UNSURE = 0.85           # below this, try the learned-text-colour read too


def colour_masked(img: Image.Image, color: list[int], tolerance: int = 60) -> Image.Image:
    """Only pixels close to the text colour, as dark text on white: removes busy,
    animated or see-through backgrounds behind the dialogue."""
    from . import preprocess
    m = preprocess.text_mask(img, "color", color, int(tolerance * 1.3))
    if m is None:
        return img
    out = np.where(m, 20, 245).astype(np.uint8)
    return Image.fromarray(out, "L").convert("RGB")


class PaddleOcr:
    def __init__(self, lang: str = "en-US", gpu: bool = False, limit: int = 960):
        self.lang = lang
        self._eng = engine(gpu, limit)
        self.last_scores: list[float] = []

    def lines(self, img: Image.Image) -> list[tuple]:
        """[(text, height, top, left, width)] in pixels of img, top to bottom; pieces of
        the same row are joined (the detector sometimes splits a line). The model's
        confidence per row is left in self.last_scores."""
        a = np.asarray(img.convert("RGB"))[:, :, ::-1].copy()
        r = self._eng(a)
        self.last_scores = []
        if r.boxes is None or not r.txts:
            return []
        items = []
        for box, txt, score in zip(r.boxes, r.txts, r.scores):
            if score < MIN_SCORE or not txt.strip():
                continue
            xs, ys = box[:, 0], box[:, 1]
            items.append([float(ys.min()), float(xs.min()), float(xs.max()), float(ys.max()),
                          _plain(txt) if _is_english(self.lang) else txt.strip(),
                          float(score)])
        items.sort(key=lambda it: (it[0] + it[3]) / 2)
        rows: list[list] = []
        for it in items:
            mid, h = (it[0] + it[3]) / 2, it[3] - it[0]
            row = rows[-1] if rows else None
            if row and abs(mid - (row[0][0] + row[0][3]) / 2) < 0.5 * max(h, 1):
                row.append(it)
            else:
                rows.append([it])
        out = []
        for row in rows:
            row.sort(key=lambda it: it[1])
            top = min(it[0] for it in row)
            bottom = max(it[3] for it in row)
            left = min(it[1] for it in row)
            right = max(it[2] for it in row)
            out.append((" ".join(it[4] for it in row), bottom - top, top, left, right - left))
            self.last_scores.append(min(it[5] for it in row))
        return out

    def read_dialogue(self, img: Image.Image, opts) -> Reading:
        raw = self.lines(img)
        used = "paddle"
        color = getattr(opts, "learned_color", None)
        if color and (not raw or min(self.last_scores, default=0) < UNSURE):
            better = self._with_learned_colour(img, raw, color, opts)
            if better is not None:
                raw, used = better, "paddle + learned text colour"
        best = Reading(raw, used)
        best.result = ocr_filter.filter_lines(raw, opts.ignore_words, opts.lang)
        best.result.top_styled = top_line_styled(img, best.result.boxes)
        return best

    def _with_learned_colour(self, img, raw, color, opts) -> list | None:
        """Re-read with only the learned dialogue colour kept. Returns the better lines,
        or None if the plain read was as good. A name line above the dialogue (often
        another colour, so it vanishes from the masked image) is kept from the plain
        read."""
        masked = self.lines(colour_masked(img, color, getattr(opts, "tolerance", 60)))
        if not masked:
            return None
        q = lambda items: ocr_filter.text_quality(" ".join(t for t, *_ in items))
        if raw and q(masked) <= q(raw) + 0.05:
            return None
        if raw:
            top = raw[0]
            first = masked[0]
            # raw top line sits clearly above the masked dialogue and is short: a name
            if top[2] + top[1] * 0.5 < first[2] and len(top[0].split()) <= 4:
                masked = [top] + masked
        return masked

    def read_name(self, img: Image.Image, opts) -> str:
        if img is None or img.width < 4 or img.height < 4:
            return ""
        got = self.lines(img)
        if not got and img.height < 60:          # tiny box: give the detector more pixels
            got = self.lines(img.resize((img.width * 2, img.height * 2), Image.LANCZOS))
        text = " ".join(t for t, *_ in got)
        return clean_name_read(text, opts.ignore_words)
