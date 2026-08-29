"""Re-download EC gallery images for one product folder (tests original/big preference)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.scrapers.playwright_base import save_brand_images
from core.scrapers.sites.julian_fashion import JulianFashionScraper


def main() -> None:
    folder = ROOT / (
        "workspace/scrape/run_20260811_092044/images/"
        "CARHARTT_WIP_Quentin_Jacket_na_340050"
    )
    url = (
        "https://www.julian-fashion.com/en-JP/product/340050/"
        "carhartt_wip/casual_jackets/quentin_jacket"
    )
    # Backup old tiny thumbs
    bak = folder / "_old_ec_thumbs"
    bak.mkdir(exist_ok=True)
    for p in folder.glob("*"):
        if p.name in {"0.png", "98.png", "99.png"} or p.suffix.lower() in {".csv", ".txt"}:
            continue
        if p.is_file() and p.suffix.lower() in {".webp", ".jpg", ".jpeg", ".png"}:
            shutil.move(str(p), str(bak / p.name))

    s = JulianFashionScraper()
    page = s.ensure_browser()
    try:
        s.ensure_member_login()
        detail = s._enrich_from_detail(url, {"href": url, "name": "", "brand": "", "price": "", "img": ""}, rank=1)
        assert detail is not None
        urls = list(detail.image_urls or [])
        print("extracted", len(urls))
        for u in urls:
            print(" ", u)
        saved = save_brand_images(urls, folder)
        print("saved", [(p.name, p.stat().st_size) for p in saved])
    finally:
        s.close_browser()


if __name__ == "__main__":
    main()
