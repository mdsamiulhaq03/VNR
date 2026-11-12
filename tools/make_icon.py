"""Generates assets/vnr.ico and assets/vnr.png (run: python tools/make_icon.py)."""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parents[1] / "assets"
S = 1024                      # draw big, downscale for crisp edges


def gradient(size, c1, c2):
    g = Image.new("RGB", (size, size))
    px = g.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))
    return g


def font(size):
    for name in ("seguibl.ttf", "segoeuib.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def layer() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def make() -> Image.Image:
    # Rounded-square badge: deep violet -> blue gradient.
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=230, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(gradient(S, (96, 58, 230), (36, 128, 240)).convert("RGBA"), (0, 0), mask)

    # Gentle glossy highlight across the top.
    gloss, g = layer()
    g.ellipse((-260, -700, S + 260, 430), fill=(255, 255, 255, 38))
    gloss.putalpha(ImageChops.multiply(gloss.getchannel("A"), mask))
    img = Image.alpha_composite(img, gloss.filter(ImageFilter.GaussianBlur(12)))

    # Translucent speech bubble (dialogue) behind the letters.
    bub, b = layer()
    b.rounded_rectangle((130, 200, 894, 700), radius=170, fill=(255, 255, 255, 52))
    b.polygon([(270, 660), (210, 850), (430, 690)], fill=(255, 255, 255, 52))
    img = Image.alpha_composite(img, bub)

    # Sound waves bottom-right, inside the badge: "read aloud".
    waves, w_ = layer()
    cx, cy = 690, 800
    for i, r in enumerate((70, 125, 180)):
        w_.arc((cx - r, cy - r, cx + r, cy + r), start=-55, end=55,
               fill=(255, 255, 255, 240 - i * 55), width=30)
    img = Image.alpha_composite(img, waves)

    # "VNR", centred in the bubble, with a soft drop shadow.
    f = font(300)
    text = "VNR"
    d = ImageDraw.Draw(img)
    l, t, r, btm = d.textbbox((0, 0), text, font=f)
    x = (S - (r - l)) / 2 - l
    y = 450 - (btm - t) / 2 - t
    shadow, s_ = layer()
    s_.text((x + 8, y + 14), text, font=f, fill=(18, 8, 70, 170))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(12)))
    ImageDraw.Draw(img).text((x, y), text, font=f, fill=(255, 255, 255, 255))
    return img


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    big = make()
    big.resize((512, 512), Image.LANCZOS).save(OUT / "vnr.png")
    big.resize((256, 256), Image.LANCZOS).save(
        OUT / "vnr.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
                                (256, 256)])
    print("wrote", OUT / "vnr.ico", OUT / "vnr.png")
