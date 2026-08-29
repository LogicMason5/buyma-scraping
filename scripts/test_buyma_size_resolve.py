"""Verify EC size extraction + Buyma size resolution."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.buyma.buyma_browser_service import BuymaBrowserSession
from core.buyma.buyma_listing_service import (
    normalize_buyma_sizes,
    resolve_listing_sizes,
    sizes_prefer_no_variation,
)
from core.scrapers.playwright_base import extract_available_sizes
from core.scrapers.sites import SCRAPER_REGISTRY


def main() -> int:
    assert normalize_buyma_sizes("S,M,L,XL") == ["S", "M", "L", "XL"]
    assert sizes_prefer_no_variation(["U"]) is True
    assert sizes_prefer_no_variation(["S", "M"]) is False

    s = SCRAPER_REGISTRY["julian-fashion"]()
    page = s.ensure_browser()
    try:
        url = (
            "https://www.julian-fashion.com/en-JP/product/340050/"
            "carhartt_wip/casual_jackets/quentin_jacket"
        )
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        sizes = extract_available_sizes(page)
        print("quentin sizes:", sizes)
        assert sizes == ["S", "M", "L", "XL"], sizes

        bag = (
            "https://www.julian-fashion.com/en-JP/product/339792/"
            "lemaire/shoulder_bags/croissant_small_shoulder_bag"
        )
        page.goto(bag, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        bag_sizes = extract_available_sizes(page)
        print("bag sizes:", bag_sizes)
        assert bag_sizes, bag_sizes
        assert sizes_prefer_no_variation(normalize_buyma_sizes(bag_sizes))
    finally:
        s.close_browser()

    row = {
        "サイズ": "指定なし",
        "仕入先URL": (
            "https://www.julian-fashion.com/en-JP/product/340050/"
            "carhartt_wip/casual_jackets/quentin_jacket"
        ),
    }
    session = BuymaBrowserSession()
    session.start()
    try:
        resolved = resolve_listing_sizes(row, fetch_sizes=session._fetch_source_sizes)
        print("resolved:", resolved)
        assert resolved == ["S", "M", "L", "XL"], resolved
    finally:
        session.close()

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
