"""Collect original/big Julian gallery URLs by clicking thumbs."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.scrapers.sites.julian_fashion import JulianFashionScraper


def main() -> None:
    url = (
        "https://www.julian-fashion.com/en-JP/product/340050/"
        "carhartt_wip/casual_jackets/quentin_jacket"
    )
    s = JulianFashionScraper()
    page = s.ensure_browser()
    try:
        s.ensure_member_login()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        found: set[str] = set()

        def harvest() -> None:
            html = page.content()
            for m in re.findall(
                r"https://julianfashionstorage[^\"'\\s]+/product/340050/(?:original|big)/conversions/[a-f0-9-]+\.webp",
                html,
                flags=re.I,
            ):
                found.add(m)
            data = page.evaluate(
                """() => {
                  const urls = [];
                  for (const img of document.querySelectorAll('img')) {
                    for (const raw of [img.currentSrc, img.src, img.srcset, img.getAttribute('data-src')]) {
                      if (!raw) continue;
                      for (const part of String(raw).split(',')) {
                        const u = part.trim().split(' ')[0];
                        if (u.includes('/product/340050/') && (u.includes('/original/') || u.includes('/big/'))) {
                          urls.push(u);
                        }
                      }
                    }
                  }
                  return urls;
                }"""
            )
            for u in data or []:
                found.add(u)

        harvest()
        thumbs = page.locator(
            ".product-detail__photos img, .product-detail__thumbs img, "
            "img[src*='/product/340050/mini/'], button:has(img[src*='/product/340050/'])"
        )
        n = min(thumbs.count(), 15)
        print("thumbs", n)
        for i in range(n):
            try:
                thumbs.nth(i).click(timeout=2500)
                page.wait_for_timeout(700)
                harvest()
            except Exception as exc:  # noqa: BLE001
                print("click fail", i, exc)
        print("found", len(found))
        out = Path("workspace/generate/_julian_gallery_urls.json")
        out.write_text(json.dumps(sorted(found), indent=2), encoding="utf-8")
        for u in sorted(found):
            print(u)
    finally:
        s.close_browser()


if __name__ == "__main__":
    main()
