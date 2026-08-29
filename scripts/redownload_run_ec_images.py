"""Re-download EC gallery images for all products in a scrape run CSV."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.csv_schema import read_products_csv
from core.scrapers.playwright_base import save_brand_images
from core.scrapers.sites import SCRAPER_REGISTRY


def site_from_url(url: str, fallback: str = "") -> str:
    u = (url or "").lower()
    if "julian-fashion" in u:
        return "julian-fashion"
    if "montiboutique" in u:
        return "montiboutique"
    if "eleonora" in u:
        return "eleonorabonucci"
    if "angelominetti" in u or "minetti" in u:
        return "minettiangeloonline"
    return fallback or "julian-fashion"


def main() -> int:
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "workspace/scrape/run_20260811_092044"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    csv_path = run / "products.csv"
    rows = read_products_csv(csv_path)
    if limit > 0:
        rows = rows[:limit]
    print(f"rows={len(rows)} run={run}", flush=True)

    scrapers: dict = {}
    ok = 0
    fail = 0
    try:
        for i, row in enumerate(rows, start=1):
            folder_name = (row.get("フォルダ名") or "").strip()
            source = (row.get("仕入先URL") or "").strip()
            code = (row.get("サイトコード") or "").strip() or site_from_url(source)
            folder = Path(row.get("画像フォルダパス") or "") if row.get("画像フォルダパス") else run / "images" / folder_name
            if not folder_name or not source:
                continue
            if not folder.is_dir():
                folder = run / "images" / folder_name
            if not folder.is_dir():
                print(f"[{i}] skip missing folder {folder_name}", flush=True)
                fail += 1
                continue

            # Skip only when every numeric EC image is already full-size.
            ec_files = [
                p
                for p in folder.iterdir()
                if p.is_file()
                and p.suffix.lower() in {".webp", ".jpg", ".jpeg", ".png"}
                and p.stem.isdigit()
                and p.name not in {"0.png", "98.png", "99.png"}
            ]
            tiny = [p for p in ec_files if p.stat().st_size < 20_000]
            large = [p for p in ec_files if p.stat().st_size >= 20_000]
            if ec_files and not tiny and len(large) >= 2:
                print(f"[{i}/{len(rows)}] skip ok {folder_name} ({len(large)} large)", flush=True)
                ok += 1
                continue

            if code not in scrapers:
                print(f"boot scraper {code}...", flush=True)
                scrapers[code] = SCRAPER_REGISTRY[code]()
                scrapers[code].ensure_browser()
                if getattr(scrapers[code], "require_member_login", False):
                    scrapers[code].ensure_member_login()

            scraper = scrapers[code]
            print(f"[{i}/{len(rows)}] {folder_name}", flush=True)
            try:
                detail = scraper._enrich_from_detail(
                    source,
                    {"href": source, "name": "", "brand": "", "price": "", "img": ""},
                    rank=1,
                )
                if not detail or not detail.image_urls:
                    print("  FAIL no urls", flush=True)
                    fail += 1
                    continue
                for p in list(folder.glob("*")):
                    if p.name in {"0.png", "98.png", "99.png"}:
                        continue
                    if p.is_file() and p.suffix.lower() in {".webp", ".jpg", ".jpeg", ".png"} and p.stem.isdigit():
                        p.unlink()
                saved = save_brand_images(list(detail.image_urls), folder)
                sizes = [p.stat().st_size for p in saved]
                print(f"  saved {len(saved)} sizes={sizes}", flush=True)
                if not saved or min(sizes) < 20000:
                    fail += 1
                else:
                    ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL {exc}", flush=True)
                fail += 1
    finally:
        for sc in scrapers.values():
            try:
                sc.close_browser()
            except Exception:  # noqa: BLE001
                pass
    print(f"done ok={ok} fail={fail}", flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
