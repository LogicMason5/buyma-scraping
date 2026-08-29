"""Engine 1 worker: scrape EC sites → products.csv (independent process)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from core.buyma.buyma_listing_service import (
    BuymaListingInput,
    buyma_folder_name,
    extract_color_from_notes,
    extract_material_origin,
    extract_sizes_from_notes,
    build_buyma_row,
)
from core.config import get_settings
from core.csv_schema import append_product_row, ensure_row, read_products_csv, write_empty_products_csv
from core.notice_images import ensure_product_notice_images
from core.scrapers.dedupe import load_known_product_keys, scraped_product_keys
from core.scrapers.playwright_base import LoginRequiredError, save_brand_images
from core.scrapers.scrape_targets import filters_for_site, load_scrape_targets
from core.scrapers.sites import SCRAPER_REGISTRY
from core.sheets.google_sheets_sync import safe_upsert_rows
from core.workbook.products_workbook import upsert_today_sheet

LogFn = Callable[[str], None]
StopFn = Callable[[], bool]


SITE_LABELS = {
    "julian-fashion": "Julian Fashion",
    "montiboutique": "Monti Boutique",
    "minettiangeloonline": "Minetti Angelo",
    "eleonorabonucci": "Eleonora Bonucci",
}

def run_scrape(
    *,
    site_codes: list[str],
    target_count: int,
    output_dir: Path | None = None,
    prefer_new: bool = True,
    brand_filter: str | None = None,
    category_filter: str | None = None,
    profit_rate: float | None = None,
    chain_engine2: bool = False,
    log: LogFn | None = None,
    should_stop: StopFn | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> Path:
    settings = get_settings()
    _log = log or (lambda m: None)
    _stop = should_stop or (lambda: False)
    _progress = on_progress or (lambda c, t, s: None)

    run_dir = output_dir or (
        settings.workspace_dir / "scrape" / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    csv_path = run_dir / "products.csv"
    write_empty_products_csv(csv_path)
    images_root = run_dir / "images"
    images_root.mkdir(parents=True, exist_ok=True)

    known = load_known_product_keys()
    _log(f"出力先: {csv_path}")
    _log(f"重複除外キー: {len(known)} 件（ワークブック / 過去CSV）")
    _log(f"取得モード: {'新着(/new|/newin)優先' if prefer_new else 'ブランド横断'}")
    saved_targets = load_scrape_targets()
    if brand_filter:
        _log(f"ブランド絞り込み(引数): {brand_filter}")
    if category_filter:
        _log(f"カテゴリ絞り込み(引数): {category_filter}")
    if not brand_filter and not category_filter:
        _log(f"収集ターゲット設定: { {k: v for k, v in saved_targets.items() if v.get('brands') or v.get('categories')} or '未設定' }")
    if profit_rate is not None:
        _log(f"利益率(今回): {profit_rate}")
    collected = 0
    remaining = max(1, int(target_count))
    skipped_dup = 0
    _progress(0, target_count, "開始")

    for code in site_codes:
        if _stop() or collected >= target_count:
            break
        scraper_cls = SCRAPER_REGISTRY.get(code)
        if not scraper_cls:
            _log(f"未知のサイトコード: {code}")
            continue
        need = remaining
        site_brand, site_cat = filters_for_site(code, saved_targets)
        use_brand = brand_filter if brand_filter is not None else site_brand
        use_cat = category_filter if category_filter is not None else site_cat
        _log(f"[{SITE_LABELS.get(code, code)}] スクレイプ開始 (最大 {need} 件)")
        if use_brand:
            _log(f"  ブランド: {use_brand}")
        if use_cat:
            _log(f"  カテゴリ: {use_cat}")
        scraper = scraper_cls()
        try:
            products = list(
                scraper.scrape_latest(
                    need,
                    prefer_new=prefer_new,
                    skip_keys=set(known),
                    brand_filter=use_brand,
                    category_filter=use_cat,
                )
            )
        except LoginRequiredError as exc:
            _log(f"ログイン必要: {exc}")
            _log(f"手動ログイン: py -3 scripts/ec_cookie_login.py {code}")
            _log("または設定画面「4 設定」→ ログイン情報の保存")
            _log("Chrome で会員ログインすると自動保存されます。横の「テスト」で登録確認できます。")
            raise
        except Exception as exc:  # noqa: BLE001
            _log(f"サイト失敗 {code}: {exc}")
            continue

        for scraped in products:
            if _stop() or collected >= target_count:
                break
            _progress(collected, target_count, f"{SITE_LABELS.get(code, code)} 収集中")
            keys = scraped_product_keys(
                external_id=scraped.external_product_id or "",
                source_url=scraped.source_url or "",
            )
            if keys & known:
                skipped_dup += 1
                continue

            color = extract_color_from_notes(getattr(scraped, "notes", None)) or ""
            sizes = extract_sizes_from_notes(getattr(scraped, "notes", None))
            material, origin = extract_material_origin(
                getattr(scraped, "description", None) or getattr(scraped, "notes", None)
            )
            stock = max(0, int(scraped.inventory if scraped.inventory is not None else 0))
            if sizes:
                stock = max(stock, len(sizes))
            listing_input = BuymaListingInput(
                brand_name=scraped.brand_name,
                product_name=scraped.name,
                source_url=scraped.source_url,
                shop_name=SITE_LABELS.get(code, scraper.site_name or code),
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
            folder = buyma_folder_name(listing_input)
            if scraped_product_keys(folder=folder) & known:
                skipped_dup += 1
                continue

            img_dir = images_root / folder
            img_dir.mkdir(parents=True, exist_ok=True)
            saved_imgs: list[Path] = []
            try:
                saved_imgs = save_brand_images(list(scraped.image_urls or []), img_dir)
            except Exception as exc:  # noqa: BLE001
                _log(f"画像保存警告: {folder}: {exc}")

            ensure_product_notice_images(img_dir, _log)
            # No description TXT — source text stays in CSV 出品メモ only.

            row = build_buyma_row(listing_input, profit_rate=profit_rate)
            # Engine1: leave Buyma comment fields empty until Engine2 fills them.
            row["商品コメント"] = ""
            row["色・サイズ補足情報"] = ""
            row["在庫"] = str(stock)
            source_desc = (listing_input.source_description or "").strip()
            if source_desc:
                memo = str(row.get("出品メモ") or "").rstrip()
                row["出品メモ"] = f"{memo}\n\n===== ソース説明 =====\n{source_desc}".strip()
            meta = ensure_row(row)
            meta["フォルダ名"] = folder
            meta["ソース画像パス"] = str(saved_imgs[0].resolve()) if saved_imgs else ""
            meta["画像フォルダパス"] = str(img_dir.resolve())
            meta["通貨"] = listing_input.currency
            meta["サイトコード"] = code
            meta["外部商品ID"] = scraped.external_product_id or ""
            meta["在庫"] = str(stock)
            meta["生成ステータス"] = "pending"
            meta["出品結果"] = ""
            meta["出品URL"] = ""
            append_product_row(csv_path, meta)
            safe_upsert_rows([meta], log=_log)
            known |= keys | scraped_product_keys(
                external_id=scraped.external_product_id or "",
                source_url=scraped.source_url or "",
                folder=folder,
            )
            collected += 1
            remaining = max(0, target_count - collected)
            _progress(collected, target_count, f"収集済み: {scraped.name}")
            _log(
                f"追加 ({collected}/{target_count}): {scraped.brand_name} / {scraped.name} "
                f"(在庫={stock})"
            )

        try:
            scraper.close_browser()
        except Exception:  # noqa: BLE001
            pass

    if collected == 0:
        _log(f"完了: 0 件 → {csv_path} (重複スキップ {skipped_dup})")
        _log("商品が追加されませんでした。ログイン切れ、ブランド／カテゴリ不一致、または既出商品の可能性があります。")
    else:
        _log(f"完了: {collected} 件 → {csv_path} (重複スキップ {skipped_dup})")
    _progress(collected, target_count, "収集完了")
    # Upsert into today's sheet ``YYYY-MM-DD (N)`` (append/update; no extra tabs per engine).
    try:
        rows = read_products_csv(csv_path) if csv_path.exists() else []
        if rows:
            wb_path, sheet_title = upsert_today_sheet(settings.products_workbook_path, rows)
            _log(f"ワークブック更新: {wb_path.name} / [{sheet_title}] ({len(rows)} 行)")
    except Exception as exc:  # noqa: BLE001
        _log(f"ワークブック保存警告: {exc}")

    if chain_engine2 and collected > 0 and csv_path.exists():
        _log("自動モード: Engine2（モデル画像生成）を開始します…")
        try:
            from apps.engine2_generate.worker import run_generate

            gen_out = settings.workspace_dir / "generate"
            run_generate(
                csv_path=csv_path,
                output_dir=gen_out,
                log=_log,
                should_stop=_stop,
            )
            _log("Engine2 完了")
        except Exception as exc:  # noqa: BLE001
            _log(f"Engine2 自動実行失敗: {exc}")

    return csv_path
