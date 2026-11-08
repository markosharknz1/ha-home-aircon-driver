"""Render the Home Aircon Driver brand PNGs with Pillow.

Mark: a white house on a cool-to-warm gradient square, with a snowflake
(cooling) on the left of the house and a flame (heating) on the right.
Drawn at 4x and downscaled for antialiasing. Output goes to the
integration's brand/ folder, which Home Assistant 2026.3+ and HACS serve
straight from the repo (no home-assistant/brands PR needed).

Run:  python docs/logo/make_brand.py
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

S = 2048  # supersampled master size (4x of 512)
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "..", "custom_components", "home_aircon_driver", "brand")

COOL = (0x2B, 0x8C, 0xE6)   # cool blue (top left)
WARM = (0xF7, 0x67, 0x07)   # warm orange (bottom right)
SNOW = (0x1C, 0x7E, 0xD6)
FLAME = (0xF0, 0x5A, 0x00)
FLAME_IN = (0xFF, 0xD4, 0x3B)
WORD = (0x1C, 0x4E, 0x80)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_mark(size=S):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    k = size / 512.0  # design coords are a 512 grid

    # cool left, warm right, with a short soft blend through the middle
    # (a straight blue->orange gradient goes muddy grey-brown halfway)
    grad = Image.new("RGBA", (size, size))
    gp = grad.load()
    for y in range(size):
        for x in range(size):
            u = (x + 0.30 * y) / (1.30 * size)            # slight diagonal
            t = min(1.0, max(0.0, (u - 0.38) / 0.24))      # blend zone 0.38 .. 0.62
            t = t * t * (3 - 2 * t)                        # smoothstep
            gp[x, y] = lerp(COOL, WARM, t) + (255,)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(112 * k), fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)

    white = (255, 255, 255, 255)

    # house: roof + body, with a small chimney
    d.polygon([(256 * k, 76 * k), (60 * k, 244 * k), (452 * k, 244 * k)], fill=white)
    d.rectangle([112 * k, 232 * k, 400 * k, 436 * k], fill=white)
    d.rectangle([348 * k, 108 * k, 392 * k, 190 * k], fill=white)

    # snowflake (left half of the house): three crossing arms with round caps
    cx, cy, r, w = 184, 338, 64, 15
    for ang in (90, 30, 150):
        a = math.radians(ang)
        x1, y1 = cx - r * math.cos(a), cy - r * math.sin(a)
        x2, y2 = cx + r * math.cos(a), cy + r * math.sin(a)
        d.line([(x1 * k, y1 * k), (x2 * k, y2 * k)], fill=SNOW + (255,), width=int(w * k))
        for px, py in ((x1, y1), (x2, y2)):
            d.ellipse([(px - w / 2) * k, (py - w / 2) * k, (px + w / 2) * k, (py + w / 2) * k],
                      fill=SNOW + (255,))
    # little side ticks on the vertical arm so it reads as a snowflake at small sizes
    for dy in (-34, 34):
        for sgn in (-1, 1):
            d.line([(cx * k, (cy + dy) * k), ((cx + sgn * 20) * k, (cy + dy - 18) * k)],
                   fill=SNOW + (255,), width=int(11 * k))

    # flame (right half): teardrop with a lighter core
    fx, fy = 328, 352
    d.ellipse([(fx - 46) * k, (fy - 14) * k, (fx + 46) * k, (fy + 66) * k], fill=FLAME + (255,))
    d.polygon([(fx * k, (fy - 98) * k), ((fx - 40) * k, (fy + 6) * k), ((fx + 40) * k, (fy + 6) * k)],
              fill=FLAME + (255,))
    d.ellipse([(fx - 22) * k, (fy + 10) * k, (fx + 22) * k, (fy + 62) * k], fill=FLAME_IN + (255,))
    d.polygon([(fx * k, (fy - 34) * k), ((fx - 20) * k, (fy + 22) * k), ((fx + 20) * k, (fy + 22) * k)],
              fill=FLAME_IN + (255,))
    return img


def find_font(size_px):
    for name in ["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"]:
        p = os.path.join(r"C:\Windows\Fonts", name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size_px)
    return ImageFont.load_default()


def make_logo():
    """Mark with a wordmark underneath, transparent background."""
    mark = make_mark(1024)
    f1 = find_font(150)
    canvas = Image.new("RGBA", (1024, 1280), (0, 0, 0, 0))
    canvas.alpha_composite(mark, (0, 0))
    d = ImageDraw.Draw(canvas)
    text = "Home Aircon Driver"
    w = d.textlength(text, font=f1)
    if w > 1000:
        f1 = find_font(int(150 * 1000 / w))
        w = d.textlength(text, font=f1)
    d.text(((1024 - w) / 2, 1070), text, font=f1, fill=WORD + (255,))
    return canvas


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    mark = make_mark()
    mark.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "icon.png"))
    mark.resize((512, 512), Image.LANCZOS).save(os.path.join(OUT, "icon@2x.png"))
    logo = make_logo()
    logo.resize((256, 320), Image.LANCZOS).save(os.path.join(OUT, "logo.png"))
    logo.resize((512, 640), Image.LANCZOS).save(os.path.join(OUT, "logo@2x.png"))
    print("wrote brand images to", os.path.normpath(OUT))
