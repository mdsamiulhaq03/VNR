"""Generates assets/vnr.ico and assets/vnr.png (run: python tools/make_icon.py).

Sleek minimal, monochrome: a flat black rounded square, a bold off-white "VNR" monogram and a
small three-bar equaliser in the app accent colour ("read aloud"). The tiny sizes
(16-32 px) get their own heavier, simpler drawing so they stay readable.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "assets"
BADGE = (0, 0, 0)             # #000000 (matches the app's monochrome design)
RIM = (46, 46, 46)            # #2E2E2E
INK = (255, 255, 255)
ACCENT = (136, 136, 136)      # #888888 equaliser bars
ACCENT_2 = (237, 237, 237)    # #EDEDED last bar


def font(size):
    for name in ("seguibl.ttf", "segoeuib.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def badge(S: int, radius: float, rim: float) -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = round(S * 0.03)
    d.rounded_rectangle((m, m, S - m - 1, S - m - 1), radius=round(S * radius), fill=BADGE)
    if rim:
        w = max(1, round(S * rim))
        d.rounded_rectangle((m + w, m + w, S - m - 1 - w, S - m - 1 - w),
                            radius=round(S * radius) - w, outline=RIM, width=w)
    return img


def centered_text(img, text, f, cy, fill, tracking=0):
    d = ImageDraw.Draw(img)
    widths = [d.textbbox((0, 0), ch, font=f)[2] - d.textbbox((0, 0), ch, font=f)[0]
              for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    x = (img.width - total) / 2
    y = cy - (b - t) / 2 - t
    for ch, w in zip(text, widths):
        cl = d.textbbox((0, 0), ch, font=f)[0]
        d.text((x - cl, y), ch, font=f, fill=fill)
        x += w + tracking


def bars(img, x0, base, unit, gap, heights, colors):
    d = ImageDraw.Draw(img)
    for i, (h, c) in enumerate(zip(heights, colors)):
        x = x0 + i * (unit + gap)
        d.rounded_rectangle((x, base - h, x + unit, base), radius=unit / 2, fill=c)


def make_large(S: int = 1024) -> Image.Image:
    img = badge(S, 0.22, 0.012)
    centered_text(img, "VNR", font(round(S * 0.36)), S * 0.44, INK, tracking=-round(S * 0.012))
    # Equaliser under the monogram.
    unit, gap = round(S * 0.055), round(S * 0.04)
    heights = [S * 0.09, S * 0.15, S * 0.07]
    x0 = (S - (3 * unit + 2 * gap)) / 2
    bars(img, x0, S * 0.80, unit, gap, heights, [ACCENT, ACCENT, ACCENT_2])
    return img


def make_small(S: int) -> Image.Image:
    """16-32 px: no rim, heavier letters ("VN"), one accent bar underneath."""
    big = 256
    img = badge(big, 0.22, 0.025)            # thin rim: visible on a dark taskbar
    centered_text(img, "VN", font(round(big * 0.56)), big * 0.45, INK,
                  tracking=-round(big * 0.03))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((big * 0.28, big * 0.76, big * 0.72, big * 0.84), radius=big * 0.04,
                        fill=ACCENT)
    return img.resize((S, S), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    big = make_large()
    big.resize((512, 512), Image.LANCZOS).save(OUT / "vnr.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [make_small(s) if s <= 32 else big.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[-1].save(OUT / "vnr.ico", format="ICO", sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])
    # Contact sheet for checking legibility.
    sheet = Image.new("RGBA", (sum(sizes) + 20 * len(sizes) + 20, 300), (30, 30, 30, 255))
    x = 20
    for s, fr in zip(sizes, frames):
        sheet.alpha_composite(fr, (x, 20))
        x += s + 20
    (OUT.parent / "build").mkdir(exist_ok=True)
    sheet.save(OUT.parent / "build" / "icon_sheet.png")       # build/ is git-ignored
    print("wrote", OUT / "vnr.ico", OUT / "vnr.png")
