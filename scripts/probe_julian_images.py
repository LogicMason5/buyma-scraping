"""Probe Julian PDP image URL patterns (full-size vs thumbs)."""

from __future__ import annotations

import json
from pathlib import Path

from core.scrapers.sites.julian_fashion import JulianFashionScraper

JS = r"""
() => {
  const out = [];
  for (const img of Array.from(document.querySelectorAll("img"))) {
    const src = img.currentSrc || img.src || "";
    const srcset = img.srcset || "";
    const ds = img.getAttribute("data-src") || "";
    const dz =
      img.getAttribute("data-zoom") ||
      img.getAttribute("data-large") ||
      img.getAttribute("data-original") ||
      "";
    const blob = (src + " " + srcset + " " + ds + " " + dz).toLowerCase();
    if (!blob.includes("product") && !blob.includes("julian") && !blob.includes("storage")) {
      continue;
    }
    out.push({
      src,
      srcset: srcset.slice(0, 400),
      ds,
      dz,
      w: img.naturalWidth || 0,
      h: img.naturalHeight || 0,
    });
  }
  for (const el of Array.from(document.querySelectorAll("source[srcset], a[href*='julianfashionstorage']"))) {
    out.push({
      tag: el.tagName,
      href: el.href || "",
      srcset: (el.getAttribute("srcset") || "").slice(0, 400),
    });
  }
  return out.slice(0, 50);
}
"""


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
        page.wait_for_timeout(3500)
        data = page.evaluate(JS)
        out = Path("workspace/generate/_julian_image_probe.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out} count={len(data)}")
        for i, d in enumerate(data[:15]):
            print(i, json.dumps(d, ensure_ascii=False)[:240])
    finally:
        s.close_browser()


if __name__ == "__main__":
    main()
