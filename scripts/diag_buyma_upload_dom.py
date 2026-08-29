"""Diagnose Buyma image uploader DOM after set_input_files."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.buyma.buyma_browser_service import BuymaBrowserSession


def main() -> int:
    folder = ROOT / "workspace/scrape/run_20260811_092044/images/LEMAIRE_Croissant_Small_Shoulder_Bag_na_339792"
    session = BuymaBrowserSession()
    session.start()
    try:
        if not session.ensure_logged_in(timeout_seconds=60):
            raise RuntimeError("login failed")
        session.open_new_listing()
        page = session.page
        assert page is not None

        before = page.evaluate(
            """() => {
              const imgs = Array.from(document.querySelectorAll('img')).map((img) => ({
                src: (img.currentSrc || img.src || '').slice(0, 140),
                w: img.naturalWidth || img.width || 0,
                h: img.naturalHeight || img.height || 0,
                cls: String(img.className || '').slice(0, 80),
                parent: img.parentElement ? String(img.parentElement.className || '').slice(0, 100) : '',
              }));
              const inputs = Array.from(document.querySelectorAll('input[type=file]')).map((i) => ({
                accept: i.accept,
                multiple: i.multiple,
                id: i.id,
                name: i.name,
                cls: i.className,
              }));
              const remain = (document.body.innerText.match(/残り\\s*\\d+\\s*枚/g) || []);
              return {imgs: imgs.slice(0, 25), inputs, remain, counterHint: imgs.length};
            }"""
        )
        print("=== BEFORE ===")
        print(json.dumps(before, ensure_ascii=False, indent=2)[:5000])

        images = BuymaBrowserSession.collect_listing_images(folder)[:4]
        work = Path(tempfile.mkdtemp(prefix="diag_buyma_"))
        prepared = session._prepare_upload_files(images, work)
        print("prepared:", [p.name for p in prepared])

        loc = page.locator("input[type='file']")
        print("file input count:", loc.count())
        loc.first.set_input_files([str(p) for p in prepared])
        page.wait_for_timeout(5000)

        after = page.evaluate(
            """() => {
              const imgs = Array.from(document.querySelectorAll('img')).map((img) => ({
                src: (img.currentSrc || img.src || '').slice(0, 160),
                w: img.naturalWidth || img.width || 0,
                h: img.naturalHeight || img.height || 0,
                cls: String(img.className || '').slice(0, 100),
                parent: img.parentElement ? String(img.parentElement.className || '').slice(0, 120) : '',
                gp: img.parentElement && img.parentElement.parentElement
                  ? String(img.parentElement.parentElement.className || '').slice(0, 120)
                  : '',
              }));
              const remain = (document.body.innerText.match(/残り\\s*\\d+\\s*枚/g) || []);
              const candidates = [];
              document.querySelectorAll('[class*="image"],[class*="Image"],[class*="upload"],[class*="Upload"],[class*="sell"],[class*="photo"],[class*="Photo"]').forEach((el) => {
                const c = String(el.className || '');
                if (c && candidates.length < 60) candidates.push(c.slice(0, 160));
              });
              return {
                imgTotal: imgs.length,
                imgs: imgs.filter((x) => x.w >= 20 || /blob:|data:|buyma|amazonaws|cloudfront/i.test(x.src)).slice(0, 40),
                remain,
                candidates: Array.from(new Set(candidates)).slice(0, 40),
              };
            }"""
        )
        out = ROOT / "workspace/generate/_buyma_upload_diag.json"
        out.write_text(json.dumps({"before": before, "after": after}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("wrote", out)
        print("legacy_counter=", session._count_uploaded_previews())
        print("remain_after=", after.get("remain"))
        print("imgTotal_after=", after.get("imgTotal"))
        print("filtered_imgs=", len(after.get("imgs") or []))
        for im in (after.get("imgs") or [])[:15]:
            print(" ", im.get("w"), im.get("h"), (im.get("src") or "")[:80], im.get("parent"))

        shot = ROOT / "workspace/generate/_buyma_upload_diag.png"
        page.screenshot(path=str(shot), full_page=False)
        print("shot=", shot)
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
