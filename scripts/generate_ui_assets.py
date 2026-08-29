"""Generate EC-Buyma UI icons (Pillow) — geometric atelier marks, no stock clipart."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "ui"


def _aa_circle(draw: ImageDraw.ImageDraw, xy, fill, outline=None, width=1):
    draw.ellipse(xy, fill=fill, outline=outline, width=width)


def brand_mark(size: int = 256) -> Image.Image:
    """Interlocking EC→B chevron mark on ink disc."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size // 16
    _aa_circle(d, [pad, pad, size - pad, size - pad], fill=(11, 31, 51, 255))
    # inner soft ring
    r = size // 10
    _aa_circle(d, [pad + r, pad + r, size - pad - r, size - pad - r], fill=(11, 31, 51, 255), outline=(214, 228, 236, 80), width=2)
    # three ascending bars = pipeline steps
    colors = [(13, 115, 119, 255), (201, 132, 42, 255), (184, 74, 74, 255)]
    w = size // 7
    gap = size // 18
    base_x = size // 3
    for i, c in enumerate(colors):
        h = size // 5 + i * (size // 9)
        x0 = base_x + i * (w + gap)
        y1 = size - size // 4
        y0 = y1 - h
        d.rounded_rectangle([x0, y0, x0 + w, y1], radius=w // 3, fill=c)
    # arrow tip
    tip = [
        (size - size // 5, size // 2),
        (int(size - size / 3.2), size // 2 - size // 10),
        (int(size - size / 3.2), size // 2 + size // 10),
    ]
    d.polygon(tip, fill=(246, 249, 251, 230))
    return img


def icon_scrape(size: int = 192) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # teal plate
    d.rounded_rectangle([8, 8, size - 8, size - 8], radius=36, fill=(13, 115, 119, 255))
    # radar arcs
    cx, cy = size // 2, size // 2 + 4
    for r in (28, 44, 60):
        d.arc([cx - r, cy - r, cx + r, cy + r], start=210, end=330, fill=(232, 247, 246, 220), width=5)
    d.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], fill=(255, 255, 255, 255))
    return img


def icon_generate(size: int = 192) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 8, size - 8, size - 8], radius=36, fill=(201, 132, 42, 255))
    # aperture / spark
    cx, cy = size // 2, size // 2
    for i in range(8):
        ang = i * (math.pi / 4)
        x0 = cx + math.cos(ang) * 18
        y0 = cy + math.sin(ang) * 18
        x1 = cx + math.cos(ang) * 58
        y1 = cy + math.sin(ang) * 58
        d.line([(x0, y0), (x1, y1)], fill=(255, 244, 220, 240), width=6)
    d.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], fill=(255, 255, 255, 255))
    d.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], fill=(201, 132, 42, 255))
    return img


def icon_list(size: int = 192) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 8, size - 8, size - 8], radius=max(12, size // 5), fill=(184, 74, 74, 255))
    # tag / ticket — scale with size
    x0, y0 = int(size * 0.24), int(size * 0.27)
    x1, y1 = int(size * 0.76), int(size * 0.73)
    d.rounded_rectangle([x0, y0, x1, y1], radius=max(6, size // 14), fill=(255, 240, 238, 255))
    hole_r = max(4, size // 20)
    cx = size // 2
    hy = int(size * 0.36)
    d.ellipse([cx - hole_r, hy - hole_r, cx + hole_r, hy + hole_r], fill=(184, 74, 74, 255))
    for frac in (0.52, 0.60, 0.68):
        y = int(size * frac)
        d.rounded_rectangle(
            [int(size * 0.32), y, int(size * 0.68), y + max(3, size // 28)],
            radius=2,
            fill=(184, 74, 74, 180),
        )
    return img


def icon_flow_dot(color: tuple[int, int, int], size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([6, 6, size - 6, size - 6], fill=(*color, 255))
    d.ellipse([18, 18, size - 18, size - 18], fill=(255, 255, 255, 220))
    return img


def bg_texture(w: int = 1200, h: int = 800) -> Image.Image:
    """Subtle diagonal mist bands for window wallpaper feel."""
    img = Image.new("RGB", (w, h), (230, 237, 242))
    d = ImageDraw.Draw(img, "RGBA")
    for i in range(-h, w + h, 28):
        d.line([(i, 0), (i + h, h)], fill=(255, 255, 255, 35), width=14)
    # soft corner glow (teal wash, not purple)
    for r in range(280, 40, -20):
        alpha = max(8, 40 - r // 10)
        d.ellipse([-r // 2, -r // 2, r, r], fill=(13, 115, 119, alpha // 3))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    brand_mark().save(OUT / "brand_mark.png")
    icon_scrape().save(OUT / "stage_1.png")
    icon_generate().save(OUT / "stage_2.png")
    icon_list().save(OUT / "stage_3.png")
    icon_flow_dot((13, 115, 119)).save(OUT / "dot_1.png")
    icon_flow_dot((201, 132, 42)).save(OUT / "dot_2.png")
    icon_flow_dot((184, 74, 74)).save(OUT / "dot_3.png")
    bg_texture().save(OUT / "bg_mist.png")
    # small toolbar glyphs
    for name, fn in (("scrape", icon_scrape), ("generate", icon_generate), ("list", icon_list)):
        fn(96).save(OUT / f"glyph_{name}.png")
    print(f"Wrote UI assets → {OUT}")


if __name__ == "__main__":
    main()
