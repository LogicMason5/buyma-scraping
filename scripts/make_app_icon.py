"""Generate Buyma-style app icon: black square, white B."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "app"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _font(size: int) -> ImageFont.ImageFont:
    for name in (
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/seguisb.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    ):
        p = Path(name)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def make_png(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    # Soft rounded corners for a modern app-icon look.
    radius = max(2, size // 8)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=(0, 0, 0, 255))
    font = _font(int(size * 0.72))
    text = "B"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) / 2 - bbox[0]
    y = (size - th) / 2 - bbox[1] - size * 0.03
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
    return img


def main() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    images = [make_png(s) for s in SIZES]
    ico_path = OUT_DIR / "ec_buyma.ico"
    png_path = OUT_DIR / "ec_buyma_256.png"
    images[-1].save(png_path)
    images[0].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=images[1:],
    )
    # Also copy as brand mark fallback for UI / window icon.
    ui_dir = ROOT / "assets" / "ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    images[-1].resize((128, 128), Image.Resampling.LANCZOS).save(ui_dir / "brand_mark.png")
    # Duplicate .ico next to UI assets for iconbitmap lookups.
    import shutil

    shutil.copy2(ico_path, ui_dir / "brand_mark.ico")
    print(f"Wrote {ico_path}")
    print(f"Wrote {png_path}")
    return ico_path


if __name__ == "__main__":
    main()
