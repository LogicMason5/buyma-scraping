"""Re-scrape a single EC product URL and merge into an existing listing row."""

from __future__ import annotations

from typing import Any

from core.buyma.buyma_listing_service import (
    BuymaListingInput,
    build_buyma_row,
    buyma_folder_name,
    extract_color_from_notes,
    extract_material_origin,
    extract_sizes_from_notes,
)
from core.csv_schema import ensure_row
from core.scrapers.sites import SCRAPER_REGISTRY


def scraped_to_listing_input(scraped, *, shop_name: str = "") -> BuymaListingInput:
    color = extract_color_from_notes(getattr(scraped, "notes", None)) or ""
    sizes = extract_sizes_from_notes(getattr(scraped, "notes", None))
    material, origin = extract_material_origin(
        getattr(scraped, "description", None) or getattr(scraped, "notes", None)
    )
    stock = max(0, int(scraped.inventory if scraped.inventory is not None else 0))
    if sizes:
        stock = max(stock, len(sizes))
    return BuymaListingInput(
        brand_name=scraped.brand_name,
        product_name=scraped.name,
        source_url=scraped.source_url,
        shop_name=shop_name or "",
        price=float(scraped.price or 0),
        currency=getattr(scraped, "currency", "EUR") or "EUR",
        price_text=getattr(scraped, "price_text", None) or scraped.display_price,
        reference_price=float(scraped.reference_price) if scraped.reference_price else None,
        product_code=getattr(scraped, "product_code", None) or scraped.model_line,
        model_line=scraped.model_line,
        category=scraped.category,
        color=color,
        size_text=",".join(sizes) if sizes else "",
        material=material,
        origin_country=origin,
        source_description=getattr(scraped, "description", None),
        external_product_id=scraped.external_product_id,
        inventory=stock,
    )


def merge_refreshed_row(existing: dict[str, Any], refreshed: dict[str, Any]) -> dict[str, str]:
    """Prefer refreshed product/price/stock fields; keep Buyma listing identity fields."""
    out = ensure_row(existing)
    keep = {
        "出品URL",
        "出品結果",
        "出品エラー",
        "生成ステータス",
        "ソース画像パス",
        "画像フォルダパス",
        "Buyma公開",
        "在庫監視",
    }
    for key, value in refreshed.items():
        if key in keep:
            continue
        if value is None or str(value).strip() == "":
            continue
        out[key] = str(value)
    # Always take fresh stock / price math when present.
    for key in ("在庫", "価格", "出品価格", "品代(海外仕入)", "BUYMA手数料", "仕入合計", "価格計算式"):
        if refreshed.get(key) not in (None, ""):
            out[key] = str(refreshed[key])
    out["在庫監視"] = "200:rescraped"
    out["Buyma公開"] = "再公開候補"
    return ensure_row(out)


def rescrape_product_with_scraper(scraper, url: str, *, existing: dict[str, Any] | None = None) -> dict[str, str] | None:
    """Use an already-logged-in scraper session to refresh one PDP into a Buyma row."""
    assert scraper.page is not None
    detail = scraper._enrich_from_detail(url, {"href": url}, rank=1)  # noqa: SLF001
    if not detail:
        return None
    shop = (existing or {}).get("仕入先保存") or (existing or {}).get("ショップ名") or scraper.site_name or ""
    listing = scraped_to_listing_input(detail, shop_name=shop)
    # Keep folder name stable when possible so workbook upsert matches.
    if existing and (existing.get("フォルダ名") or "").strip():
        folder = existing["フォルダ名"]
    else:
        folder = buyma_folder_name(listing)
    priced = build_buyma_row(listing)
    priced["フォルダ名"] = folder
    priced["サイトコード"] = scraper.site_code
    priced["外部商品ID"] = detail.external_product_id or ""
    priced["通貨"] = detail.currency or "EUR"
    if existing:
        return merge_refreshed_row(existing, priced)
    return ensure_row(priced)


def rescrape_product_url(url: str, *, site_code: str, existing: dict[str, Any] | None = None) -> dict[str, str] | None:
    """Standalone rescrape (opens its own browser). Prefer session reuse in monitor."""
    cls = SCRAPER_REGISTRY.get(site_code)
    if not cls:
        return None
    scraper = cls()
    try:
        scraper.ensure_browser()
        if getattr(scraper, "require_member_login", True):
            scraper.ensure_member_login()
        else:
            try:
                scraper.login()
            except Exception:  # noqa: BLE001
                pass
        return rescrape_product_with_scraper(scraper, url, existing=existing)
    finally:
        try:
            scraper.close_browser()
        except Exception:  # noqa: BLE001
            pass
