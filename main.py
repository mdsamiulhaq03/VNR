"""VN Reader: reads visual novel text out loud from an area of your screen.

Run:  .venv\\Scripts\\python main.py      (or the built "VN Reader.exe")
"""
import sys


def selftest(out_path: str) -> int:
    """Check OCR + Windows voice work (used to verify the packaged .exe)."""
    import io
    import traceback

    lines = []
    try:
        from PIL import Image, ImageDraw, ImageFont
        import soundfile as sf

        from app.sources.ocr_source import WindowsOcr
        from app.tts.sapi_fallback import SapiEngine

        img = Image.new("RGB", (700, 90), (30, 30, 50))
        try:
            font = ImageFont.truetype("arial.ttf", 32)
        except OSError:
            font = ImageFont.load_default()
        ImageDraw.Draw(img).text((15, 25), "Self test line for VN Reader.", fill="white",
                                 font=font)
        lines.append("ocr: " + WindowsOcr("en-US").read(img))
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
        from app.ui import theme
        fonts_ok = theme.load_fonts()
        import os
        icons = [f for f in os.listdir(theme.asset_dir("icons")) if f.endswith(".svg")]
        lines.append(f"ui: fonts={theme.SANS}/{theme.MONO} ({'bundled' if fonts_ok else 'fallback'})"
                     f", icons={len(icons)}, play icon ok={not theme.pixmap('play').isNull()}")

        from app.name_split import split_name
        from app.voices import guess_gender
        img2 = Image.new("RGB", (700, 130), (30, 30, 50))
        d = ImageDraw.Draw(img2)
        d.text((15, 10), "Sayori", fill="white", font=font)
        d.text((15, 70), "Hey, wait up for me!", fill="white", font=font)
        who, what = split_name(WindowsOcr("en-US").read_lines(img2), [], "auto")
        lines.append(f"name split: {who!r} / {what!r} / gender={guess_gender(who)}")
        from app.sources.ocr_source import OcrOptions
        box = Image.new("RGB", (320, 66), (30, 25, 45))
        d = ImageDraw.Draw(box)
        d.rounded_rectangle((10, 8, 300, 58), 12, fill=(230, 120, 170))
        d.text((30, 14), "Rin", fill="white", font=font)
        lines.append("name area: " + repr(WindowsOcr("en-US").read_name(box, OcrOptions())))
        from app.sources.ocr_source import make_ocr
        po = make_ocr(OcrOptions(engine="paddle"))
        lines.append(f"paddleocr ({type(po).__name__}): " + repr(po.read(img) if hasattr(
            po, "read") else " ".join(po.read_dialogue(img, OcrOptions()).lines)) +
            " / name " + repr(po.read_name(box, OcrOptions())))
        from app.sources import paddle_ocr
        from app.sources.screenshot_reader import find_dialogue
        lines.append(f"gpu ocr available: {paddle_ocr.gpu_available()}")
        shot = Image.new("RGB", (1280, 720), (60, 70, 90))
        d = ImageDraw.Draw(shot)
        d.text((20, 15), "Save  Load  Auto  Skip", fill="white", font=font)
        d.rectangle((60, 520, 1220, 700), fill=(20, 20, 40))
        d.text((80, 470), "Kaelith", fill=(255, 150, 200), font=font)
        d.text((90, 560), "Welcome to Eldoria, traveller.", fill="white", font=font)
        o = OcrOptions(engine="paddle")
        r = find_dialogue(shot, make_ocr(o), make_ocr(o, limit=1920, gpu=True), o)
        lines.append(f"screenshot: name={r.info.get('name')!r} text={' '.join(r.lines)!r}")

        from app.presets import defaults
        from app.sources.ocr_filter import correct, filter_lines
        from app.config import DEFAULT_IGNORE_WORDS
        ps = defaults()
        lines.append(f"presets: {sum(p['gender'] == 'male' for p in ps)} male, "
                     f"{sum(p['gender'] == 'female' for p in ps)} female")
        kept = filter_lines([("Sayori", 30), ("Tbe rnorning!", 30),
                             ("Back History Skip Auto Save", 30), ("l1 ~ ;:", 30)],
                            DEFAULT_IGNORE_WORDS).lines
        lines.append(f"ocr filter: {kept} -> {correct(' '.join(kept[1:]), ['Sayori'])[0]!r}")
        from app.emotion import add_tags
        lines.append("emotion: " + add_tags("Ahaha! Eh?! *sigh*", {"[laughter]", "[sigh]",
                                                                  "[question-ei]"}))
        lines.append("adult words kept: " + correct("Nngh... Senpai, haaah, fuck!", [])[0])
        from app import winapi
        lines.append(f"advance keys: {sorted(winapi.ADVANCE_KEYS)}")

        wav = SapiEngine().synth("Self test.", "", pitch=3)
        data, sr = sf.read(io.BytesIO(wav))
        lines.append(f"sapi: {len(data) / sr:.2f}s")
        from app.tts.voicestudio import VoiceStudioEngine
        vs = VoiceStudioEngine()
        if vs.available():
            wav = vs.synth("Self test.", "default", instruct="female, young adult, high pitch",
                           seed=7)
            data, sr = sf.read(io.BytesIO(wav))
            lines.append(f"voicestudio designed voice: {len(data) / sr:.2f}s")
        else:
            lines.append("voicestudio: offline")
        import sounddevice as sd
        lines.append(f"audio devices: {len(sd.query_devices())}")
        code = 0
    except Exception:
        lines.append(traceback.format_exc())
        code = 1
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return code


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2])

    from PySide6.QtWidgets import QApplication

    from app.config import Config
    from app.ui.main_window import MainWindow

    from app.ui import theme

    import ctypes
    import os

    from PySide6.QtGui import QIcon

    # Own taskbar identity, so Windows shows our icon instead of Python's.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VNReader.App")
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("VN Reader")
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    icon = os.path.join(base, "assets", "vnr.ico")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    theme.apply(app)
    win = MainWindow(Config())
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
