"""Live upload full gallery for one fixed product folder."""

from __future__ import annotations

import sys
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
        images = BuymaBrowserSession.collect_listing_images(folder)
        print("order", [p.name for p in images], "n", len(images))
        assert images[0].name == "0.png"
        assert images[1].name == "98.png"
        assert images[-1].name == "99.png"
        assert len(images) >= 6, images

        n = session._upload_images(images)
        after = session._count_uploaded_previews()
        print("uploaded", n, "visible", after)
        assert after == len(images) == n

        shot = ROOT / "workspace/generate/_buyma_upload_full.png"
        session.page.screenshot(path=str(shot), full_page=False)
        urls = session.page.evaluate(
            """() => Array.from(document.querySelectorAll(
                 '.message-gallery__thumb--image img, .message-gallery__thumb img'
               )).map((i) => i.currentSrc || i.src)"""
        )
        print("preview_count", len(urls))
        for i, u in enumerate(urls):
            print(i, (u or "")[-70:])
        print("PASS", shot)
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
