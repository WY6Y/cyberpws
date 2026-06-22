#!/usr/bin/env python3
"""Generate cyberpunk PWA icons for WY6Y Weather."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent / "static"
BG = (5, 5, 5)
CYAN = (0, 240, 255)
GREEN = (0, 255, 159)
MAGENTA = (255, 0, 170)
YELLOW = (255, 255, 0)


def _grid(draw: ImageDraw.ImageDraw, size: int, step: int = 32):
    for x in range(0, size, step):
        draw.line([(x, 0), (x, size)], fill=(0, 240, 255, 28), width=1)
    for y in range(0, size, step):
        draw.line([(0, y), (size, y)], fill=(0, 240, 255, 28), width=1)


def _glow_dot(base: Image.Image, xy, color, radius: int):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = xy
    for r, alpha in ((radius * 2, 30), (radius, 90), (max(2, radius // 2), 220)):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*color, alpha))
    return Image.alpha_composite(base, layer)


def render_icon(size: int, maskable: bool = False) -> Image.Image:
    img = Image.new("RGBA", (size, size), (*BG, 255))
    draw = ImageDraw.Draw(img)
    pad = int(size * 0.12) if maskable else int(size * 0.06)
    inner = size - pad * 2

    # Frame
    draw.rounded_rectangle(
        (pad, pad, size - pad, size - pad),
        radius=int(size * 0.14),
        outline=CYAN,
        width=max(2, size // 64),
    )
    _grid(draw, size, max(16, size // 8))

    cx, cy = size // 2, int(size * 0.46)

    # Radio arcs (WeatherThief / rtl_433 vibe)
    for i, col in enumerate((CYAN, MAGENTA, GREEN)):
        r = int(inner * (0.22 + i * 0.08))
        bbox = (cx - r, cy - r, cx + r, cy + r)
        draw.arc(bbox, start=200, end=340, fill=col, width=max(2, size // 48))

    # Thermometer body
    tw = max(8, size // 16)
    th = int(size * 0.34)
    tx = cx - tw // 2
    ty = cy - th // 2
    draw.rounded_rectangle((tx, ty, tx + tw, ty + th), radius=tw // 2, fill=(10, 20, 24, 255), outline=CYAN, width=max(2, size // 80))
    bulb = int(tw * 1.35)
    draw.ellipse((cx - bulb // 2, ty + th - bulb // 3, cx + bulb // 2, ty + th + bulb // 2), fill=MAGENTA, outline=YELLOW, width=max(2, size // 96))
    fill_h = int(th * 0.62)
    draw.rectangle((tx + 2, ty + th - fill_h, tx + tw - 2, ty + th - 4), fill=YELLOW)

    # WX monogram
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(size * 0.17))
    except OSError:
        font = ImageFont.load_default()
    label = "WX"
    bbox = draw.textbbox((0, 0), label, font=font)
    tw_txt = bbox[2] - bbox[0]
    th_txt = bbox[3] - bbox[1]
    draw.text((cx - tw_txt // 2, int(size * 0.72) - th_txt // 2), label, font=font, fill=GREEN)

    img = _glow_dot(img, (cx, ty + th - fill_h // 2), YELLOW, max(6, size // 24))
    img = img.filter(ImageFilter.SMOOTH_MORE)
    return img


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    sizes = {
        "icon-192.png": (192, False),
        "icon-512.png": (512, False),
        "icon-maskable-512.png": (512, True),
        "apple-touch-icon.png": (180, False),
        "favicon-32.png": (32, False),
    }
    for name, (size, maskable) in sizes.items():
        render_icon(size, maskable=maskable).convert("RGB").save(ROOT / name, optimize=True)
        print(f"wrote {name}")

    # favicon.ico from 32px
    fav = Image.open(ROOT / "favicon-32.png")
    fav.save(ROOT / "favicon.ico", format="ICO", sizes=[(32, 32)])
    print("wrote favicon.ico")


if __name__ == "__main__":
    main()