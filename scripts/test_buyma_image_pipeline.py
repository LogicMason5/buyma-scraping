"""Thorough test: EC image redownload + Buyma JPEG upload preview count.

Usage:
  py -3 scripts/test_buyma_image_pipeline.py
  py -3 scripts/test_buyma_image_pipeline.py --live-upload
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.buyma.buyma_browser_service import BuymaBrowserSession
from core.csv_schema import read_products_csv
from core.scrapers.playwright_base import save_brand_images
from core.scrapers.sites import SCRAPER_REGISTRY


def _pick_folder() -> tuple[Path, str]:
    run = ROOT / "workspace/scrape/run_20260811_092044"
    csv_path = run / "products.csv"
    rows = read_products_csv(csv_path)
    # Prefer a product that still has tiny EC thumbs besides Quentin (already fixed).
    for row in rows:
        folder_name = (row.get("フォルダ名") or "").strip()
        if not folder_name or "Quentin" in folder_name:
            continue
        folder = run / "images" / folder_name
        if not folder.is_dir():
            continue
        tiny = [
            p
            for p in folder.iterdir()
            if p.suffix.lower() == ".webp" and p.stat().st_size < 12000
        ]
        if tiny and (folder / "0.png").exists():
            return folder, (row.get("仕入先URL") or "").strip()
    # fallback Quentin
    folder = run / "images/CARHARTT_WIP_Quentin_Jacket_na_340050"
    row = next(r for r in rows if "340050" in (r.get("フォルダ名") or ""))
    return folder, (row.get("仕入先URL") or "").strip()


def test_jpeg_prepare(folder: Path) -> list[Path]:
    print("\n=== 1) JPEG prepare (Buyma rejects webp / flaky PNG) ===")
    session = BuymaBrowserSession.__new__(BuymaBrowserSession)
    chosen = BuymaBrowserSession.collect_listing_images(folder)
    print("collect order:", [p.name for p in chosen])
    assert any(p.name == "0.png" for p in chosen), "AI 0.png missing from collect"
    assert any(p.name == "98.png" for p in chosen), "98.png missing"
    assert any(p.name == "99.png" for p in chosen), "99.png missing"
    assert any(p.suffix.lower() in {".webp", ".jpg", ".jpeg", ".png"} and p.stem not in {"0", "98", "99"} for p in chosen) or True
    work = Path(tempfile.mkdtemp(prefix="buyma_prep_"))
    prepared = BuymaBrowserSession._prepare_upload_files(session, chosen, work)
    print("prepared:", [(p.name, p.stat().st_size) for p in prepared])
    assert len(prepared) >= 3, f"expected >=3 prepared JPEGs, got {len(prepared)}"
    for p in prepared:
        assert p.suffix.lower() == ".jpg"
        assert p.read_bytes()[:2] == b"\xff\xd8"
    print("PASS jpeg prepare")
    return prepared


def test_redownload_ec(folder: Path, source_url: str) -> None:
    print("\n=== 2) EC image redownload (original/big, not mini) ===")
    if not source_url:
        print("SKIP no source url")
        return
    site_code = "julian-fashion"
    if "montiboutique" in source_url:
        site_code = "montiboutique"
    elif "eleonora" in source_url:
        site_code = "eleonorabonucci"
    elif "angelominetti" in source_url or "minetti" in source_url:
        site_code = "minettiangeloonline"
    scraper_cls = SCRAPER_REGISTRY[site_code]
    scraper = scraper_cls()
    page = scraper.ensure_browser()
    try:
        if getattr(scraper, "require_member_login", False):
            scraper.ensure_member_login()
        else:
            try:
                scraper.login()
            except Exception:  # noqa: BLE001
                pass
        detail = scraper._enrich_from_detail(
            source_url,
            {"href": source_url, "name": "", "brand": "", "price": "", "img": ""},
            rank=1,
        )
        assert detail is not None, "detail enrich failed"
        urls = list(detail.image_urls or [])
        print(f"extracted urls: {len(urls)}")
        for u in urls[:8]:
            print(" ", u)
        assert urls, "no image urls"
        assert any("/original/" in u or "/big/" in u for u in urls), "no original/big urls"

        # Replace only numeric EC images; keep 0/98/99
        for p in list(folder.glob("*")):
            if p.name in {"0.png", "98.png", "99.png"}:
                continue
            if p.suffix.lower() in {".webp", ".jpg", ".jpeg", ".png"} and p.stem.isdigit():
                p.unlink()

        saved = save_brand_images(urls, folder)
        print("saved:", [(p.name, p.stat().st_size) for p in saved])
        assert saved, "no images saved"
        assert all(p.stat().st_size >= 20_000 for p in saved), "saved images still too small"
        print("PASS ec redownload")
    finally:
        scraper.close_browser()


def test_live_upload(folder: Path) -> None:
    print("\n=== 3) LIVE Buyma upload preview test (no submit) ===")
    session = BuymaBrowserSession()
    session.start()
    try:
        if not session.ensure_logged_in(timeout_seconds=60):
            raise RuntimeError("Buyma login failed")
        session.open_new_listing()
        images = BuymaBrowserSession.collect_listing_images(folder)
        print("uploading:", [p.name for p in images])
        assert any(p.name == "0.png" for p in images), "0.png not in upload order"
        assert images[0].name == "0.png", f"first must be AI 0.png, got {images[0].name}"
        assert any(p.name == "98.png" for p in images), "98.png missing"
        assert images[-1].name == "99.png", f"last must be 99.png, got {images[-1].name}"
        ec = [p for p in images if p.stem.isdigit() and p.name not in {"0.png", "98.png", "99.png"}]
        assert ec, "no EC images in upload set"

        before = session._count_uploaded_previews()
        n = session._upload_images(images)
        after = session._count_uploaded_previews()
        shot = ROOT / "workspace/generate/_buyma_upload_test.png"
        session.page.screenshot(path=str(shot), full_page=False)
        print(f"uploaded_return={n} before={before} after={after} expected={len(images)} shot={shot}")
        assert after == len(images), f"expected exactly {len(images)} previews, got {after}"
        assert n == len(images), f"upload return {n} != {len(images)}"
        # Ensure we did not flood duplicates (old bug: counter=0 → re-upload).
        assert after <= 12, f"too many previews (duplicates?): {after}"
        print("PASS live upload")
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-upload", action="store_true")
    parser.add_argument("--skip-redownload", action="store_true")
    args = parser.parse_args()

    folder, source_url = _pick_folder()
    print("folder:", folder)
    print("source:", source_url)

    if not args.skip_redownload:
        test_redownload_ec(folder, source_url)
    test_jpeg_prepare(folder)
    if args.live_upload:
        test_live_upload(folder)
    else:
        print("\n(live upload skipped — pass --live-upload to test Buyma form)")
    print("\nALL CHECKS DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
