"""Engine 2 worker: CSV → model image into Engine1 folder + update CSV comments.

No ChatGPT description channel. Final products_ready.csv is written under generate/.
"""

from __future__ import annotations

import hashlib
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.buyma.buyma_listing_service import (
    BuymaListingInput,
    apply_listing_defaults,
    build_buyma_row,
    extract_color_from_notes,
    extract_material_origin,
    write_listing_csv_beside_images,
)
from core.chatgpt.chatgpt_browser_service import ChatGPTBrowserSession
from core.chatgpt.chatgpt_http_service import ChatGPTHttpSession
from core.config import get_settings
from core.notice_images import ensure_product_notice_images
from core.csv_schema import read_products_csv, write_products_csv, ensure_row
from core.prompts import default_image_prompt
from core.sheets.google_sheets_sync import safe_push_csv, safe_upsert_rows
from core.workbook.products_workbook import update_workbook_rows

LogFn = Callable[[str], None]
StopFn = Callable[[], bool]


def _gen_status(row: dict) -> str:
    return (row.get("生成ステータス") or "").strip().lower()


def _resolve_image_dir(row: dict) -> Path | None:
    folder = (row.get("画像フォルダパス") or "").strip()
    if folder:
        path = Path(folder)
        if path.is_dir():
            return path
    source = (row.get("ソース画像パス") or "").strip()
    if source:
        parent = Path(source).parent
        if parent.is_dir():
            return parent
    return None


SOURCE_DESC_MARKER = "===== ソース説明 ====="


_MODEL_VARIANTS: list[dict[str, str]] = [
    {"hair": "ショートヘア", "vibe": "知的で洗練・スリム体型", "pose": "首都の大通りを歩く全身"},
    {"hair": "ミディアムウェーブ", "vibe": "明るく都会的・標準体型", "pose": "観光地の広場で軽く振り向く"},
    {"hair": "ロングストレート", "vibe": "エレガント・長身", "pose": "リゾートの海岸沿い立ち姿"},
    {"hair": "センターパート", "vibe": "モード系・細身", "pose": "人気都市の交差点で動きのある半身"},
    {"hair": "軽いパーマ", "vibe": "ナチュラル・柔らかな体型", "pose": "リゾートプールサイドを歩く"},
    {"hair": "アップスタイル", "vibe": "上品・グラマラス", "pose": "歴史的観光地の石畳でポーズ"},
    {"hair": "ボブカット", "vibe": "モダン・小柄", "pose": "賑やかな首都のカフェ通り"},
    {"hair": "ウルフカット", "vibe": "ストリート・アスレチック体型", "pose": "人気都市の橋の上を歩く"},
    {"hair": "黒髪ロング", "vibe": "クラシック・均等体型", "pose": "観光ランドマーク前の足元寄り"},
    {"hair": "ショートマッシュ", "vibe": "クール・がっしりめ", "pose": "首都の広場で正面寄り半身"},
]


def _model_profile_for_item(folder: str, product_name: str) -> tuple[str, str]:
    base = f"{folder}|{product_name}"
    n = int(hashlib.sha1(base.encode("utf-8")).hexdigest()[:8], 16)
    v = _MODEL_VARIANTS[n % len(_MODEL_VARIANTS)]
    model_id = f"M{(n % 900) + 100}"
    profile = f"{v['hair']}/{v['vibe']}/{v['pose']}"
    return model_id, profile


# Calibrated to assets/reference_listing_frame_herno.png:
# square; left ~14%; bottom ~4%; gray #C8C8C8;
# brand: Didot-like serif, correct Latin baseline, ~93% strip width, ~58% height max.
_BOTTOM_BANNER_TEXT = "関税なし＆送料無料、国内発送！"
_FRAME_GRAY_RGB = (200, 200, 200)
_FRAME_TEXT = (0, 0, 0, 255)
_STRIP_RATIO = 0.140
_BOTTOM_RATIO = 0.040
_BRAND_HEIGHT_RATIO = 0.88  # allow long brands to grow until strip width fills
_BRAND_GLYPH_RATIO = 0.94

_OUTPUT_SIZE = 1536


def _to_square_rgb(img: "Image.Image", size: int = _OUTPUT_SIZE) -> "Image.Image":
    """Center-crop to square and upscale to a stable high-res canvas."""
    from PIL import Image

    rgb = img.convert("RGB")
    w, h = rgb.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    cropped = rgb.crop((left, top, left + side, top + side))
    if cropped.size[0] != size:
        cropped = cropped.resize((size, size), Image.Resampling.LANCZOS)
    return cropped


def _apply_listing_frame(img: "Image.Image", brand_name: str) -> "Image.Image":
    """Compose layout matching assets/reference_listing_frame_herno.png."""
    from PIL import Image, ImageDraw, ImageFont

    brand = (brand_name or "").strip()
    photo = _to_square_rgb(img)
    w = h = _OUTPUT_SIZE
    strip_w = max(1, int(round(w * _STRIP_RATIO)))
    bottom_h = max(1, int(round(h * _BOTTOM_RATIO)))
    content_w = max(1, w - strip_w)
    content_h = max(1, h - bottom_h)

    # Fresh canvas: gray frame first, then photo only in the content window.
    canvas = Image.new("RGB", (w, h), _FRAME_GRAY_RGB)
    fitted = photo.resize((content_w, content_h), Image.Resampling.LANCZOS)
    canvas.paste(fitted, (strip_w, 0))

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    def _load_font(candidates: tuple[str, ...], size: int):
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:  # noqa: BLE001
                continue
        return ImageFont.load_default()

    # High-contrast display serif (Herno/Didot-like). Prefer Bold for ink weight.
    serif_fonts = (
        "C:/Windows/Fonts/georgiab.ttf",  # Georgia Bold
        "C:/Windows/Fonts/Georgia.ttf",
        "C:/Windows/Fonts/georgia.ttf",
        "C:/Windows/Fonts/constanb.ttf",
        "C:/Windows/Fonts/pala.ttf",
        "C:/Windows/Fonts/timesbd.ttf",
        "C:/Windows/Fonts/times.ttf",
    )
    gothic_fonts = (
        "C:/Windows/Fonts/YuGothR.ttc",
        "C:/Windows/Fonts/YuGothM.ttc",
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
    )

    if brand:
        # Prefer filling strip WIDTH with large glyphs. Long names may be
        # slightly condensed horizontally so letters stay big (not tiny).
        max_glyph = max(12, int(strip_w * _BRAND_GLYPH_RATIO))
        max_span = max(40, int(h * _BRAND_HEIGHT_RATIO))
        measure_draw = ImageDraw.Draw(Image.new("RGBA", (8, 8), (0, 0, 0, 0)))

        def _measure(fs: int) -> tuple[int, int, object]:
            f = _load_font(serif_fonts, max(8, fs))
            bb = measure_draw.textbbox((0, 0), brand, font=f)
            return bb[2] - bb[0], bb[3] - bb[1], f

        # Size by glyph height first (big letters in strip).
        lo, hi = 8, max(8, max_glyph * 5)
        best_fs, tw, th, brand_font = 8, 0, 0, _load_font(serif_fonts, 8)
        while lo <= hi:
            mid = (lo + hi) // 2
            mw, mh, mf = _measure(mid)
            if mh <= max_glyph:
                best_fs, tw, th, brand_font = mid, mw, mh, mf
                lo = mid + 1
            else:
                hi = mid - 1
        if tw <= 0 or th <= 0:
            tw, th, brand_font = _measure(best_fs)

        bb0 = measure_draw.textbbox((0, 0), brand, font=brand_font)
        pad = 2
        text_img = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
        ImageDraw.Draw(text_img).text(
            (pad - bb0[0], pad - bb0[1]),
            brand,
            fill=_FRAME_TEXT,
            font=brand_font,
        )
        # Condense horizontally if too tall after rotate (keeps letter size large).
        if text_img.size[0] > max_span:
            text_img = text_img.resize(
                (max_span, text_img.size[1]),
                Image.Resampling.LANCZOS,
            )

        rotated = text_img.rotate(90, expand=True, resample=Image.Resampling.BICUBIC)
        rw, rh = rotated.size
        scale = min(strip_w / max(rw, 1), h / max(rh, 1), 1.0)
        # Nudge up to target glyph fill when possible.
        target_w = int(strip_w * _BRAND_GLYPH_RATIO)
        if rw < target_w and rh * (target_w / max(rw, 1)) <= max_span:
            scale = target_w / max(rw, 1)
        if abs(scale - 1.0) > 1e-3:
            rotated = rotated.resize(
                (max(1, int(rw * scale)), max(1, int(rh * scale))),
                Image.Resampling.LANCZOS,
            )
            rw, rh = rotated.size
        if rw > strip_w:
            rotated = rotated.resize(
                (strip_w, max(1, int(rh * strip_w / rw))),
                Image.Resampling.LANCZOS,
            )
            rw, rh = rotated.size
        if rh > h:
            rotated = rotated.resize(
                (max(1, int(rw * h / rh)), h),
                Image.Resampling.LANCZOS,
            )
            rw, rh = rotated.size
        overlay.paste(rotated, ((strip_w - rw) // 2, (h - rh) // 2), rotated)

    # Bottom JP gothic: ~40% of bar height, centered on FULL width (Herno).
    bottom_font = _load_font(gothic_fonts, max(10, int(bottom_h * 0.40)))
    try:
        bbox = draw.textbbox((0, 0), _BOTTOM_BANNER_TEXT, font=bottom_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:  # noqa: BLE001
        tw, th = len(_BOTTOM_BANNER_TEXT) * 10, bottom_h // 2
    if tw > int(w * 0.78):
        shrink = (w * 0.78) / max(tw, 1)
        bottom_font = _load_font(gothic_fonts, max(9, int(bottom_h * 0.40 * shrink)))
        bbox = draw.textbbox((0, 0), _BOTTOM_BANNER_TEXT, font=bottom_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    bx = max(0, (w - tw) // 2)
    by = h - bottom_h + max(1, (bottom_h - th) // 2)
    draw.text((bx, by), _BOTTOM_BANNER_TEXT, fill=_FRAME_TEXT, font=bottom_font)
    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


def _ensure_video_style_model_photo(image_dir: Path, *, brand_name: str = "") -> Path:
    """Normalize AI photo to square high-res 0.jpg with fixed listing frame."""
    src_candidates = [image_dir / "0.png", image_dir / "0.jpg", image_dir / "0.jpeg"]
    src = next((p for p in src_candidates if p.exists()), None)
    if src is None:
        raise FileNotFoundError("0 image not found")
    try:
        from PIL import Image

        img = Image.open(src)
        img = _apply_listing_frame(img, brand_name)
        out = image_dir / "0.jpg"
        img.save(out, format="JPEG", quality=95, optimize=True, subsampling=0)
        try:
            for extra in (image_dir / "0.png", image_dir / "0.jpeg"):
                if extra.exists() and extra.resolve() != out.resolve():
                    extra.unlink()
        except Exception:  # noqa: BLE001
            pass
        return out
    except Exception:
        return src


def _fallback_model_photo_from_source(
    image_dir: Path, src_path: Path, *, brand_name: str = ""
) -> Path | None:
    """When image generation fails, keep pipeline alive with source-based 0.jpg."""
    try:
        from PIL import Image

        out = image_dir / "0.jpg"
        img = Image.open(src_path).convert("RGB")
        img = _apply_listing_frame(img, brand_name)
        img.save(out, format="JPEG", quality=90, optimize=True)
        return out
    except Exception:
        try:
            out = image_dir / "0.jpg"
            shutil.copy2(src_path, out)
            return out
        except Exception:
            return None


def _load_source_description(image_dir: Path, row: dict) -> str:
    """Prefer scrape text stored in CSV 出品メモ; optional legacy TXT is fallback only."""
    memo = (row.get("出品メモ") or "").strip()
    if SOURCE_DESC_MARKER in memo:
        text = memo.split(SOURCE_DESC_MARKER, 1)[1].strip()
        if text:
            return text
    # Legacy folder TXT (optional compatibility)
    legacy = image_dir / "source_description.txt"
    if legacy.exists():
        text = legacy.read_text(encoding="utf-8").strip()
        if text:
            return text
    for key in ("商品コメント",):
        text = (row.get(key) or "").strip()
        if text:
            return text
    return ""


@dataclass
class _ItemCtx:
    idx: int
    row: dict
    folder: str
    image_dir: Path
    src_path: Path | None
    listing_input: BuymaListingInput
    prompt_vars: dict
    currency: str
    listing_price: float


def _build_item(idx: int, row: dict) -> _ItemCtx | None:
    settings = get_settings()
    image_dir = _resolve_image_dir(row)
    if image_dir is None:
        return None

    folder = row.get("フォルダ名") or image_dir.name
    source_image = (row.get("ソース画像パス") or "").strip()
    src_path = Path(source_image) if source_image else None
    if src_path is None or not src_path.exists():
        # Prefer numbered product shot in folder (not 98/99 common notices)
        for p in sorted(image_dir.iterdir()):
            if p.is_file() and p.stem.isdigit() and int(p.stem) < 90:
                if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    src_path = p
                    break

    color = (
        row.get("色")
        or extract_color_from_notes(row.get("出品メモ"))
        or row.get("カラー系統")
        or ""
    )
    material, origin = extract_material_origin(row.get("出品メモ") or row.get("商品コメント"))
    try:
        price = float(str(row.get("品代(海外仕入)") or "0").replace(",", "") or 0)
    except ValueError:
        price = 0.0
    currency = row.get("通貨") or "EUR"
    try:
        raw_price = str(row.get("価格") or "0").replace(",", "")
        listing_price = float(raw_price or 0)
    except ValueError:
        listing_price = 0.0

    from core.buyma.buyma_listing_service import source_amount_from_product_jpy

    # 品代(海外仕入) is always JPY — convert back to source currency.
    source_amount = source_amount_from_product_jpy(price, currency)

    source_desc = _load_source_description(image_dir, row)
    try:
        inventory = int(str(row.get("在庫") or "0").replace(",", "").strip() or "0")
    except ValueError:
        inventory = 0
    if inventory <= 0:
        inventory = 1
    listing_input = BuymaListingInput(
        brand_name=row.get("ブランド") or "",
        product_name=(row.get("商品名") or "").replace("【", "").split("】")[-1].replace(" 関税無＆送料無料", "").strip()
        or (row.get("商品名") or ""),
        source_url=row.get("仕入先URL") or "",
        shop_name=row.get("仕入先保存") or row.get("ショップ名") or "",
        price=source_amount or 1.0,
        currency=currency,
        reference_price=None,
        product_code=row.get("SKU") or row.get("型番/メモ") or "",
        model_line=row.get("モデル・ライン") or "",
        category=row.get("カテゴリ") or "",
        color=color,
        size_text=row.get("サイズ") or "",
        material=material,
        origin_country=origin,
        source_description=source_desc or None,
        ai_comment=source_desc or None,
        external_product_id=row.get("外部商品ID") or "",
        inventory=inventory,
    )
    prompt_vars = {
        "brand_name": listing_input.brand_name,
        "product_name": listing_input.product_name,
        "category": listing_input.category or "",
        "color": listing_input.color or "",
        "material": listing_input.material or "",
        "origin_country": listing_input.origin_country or "",
        "source_url": listing_input.source_url,
    }
    model_id, model_profile = _model_profile_for_item(str(folder), listing_input.product_name)
    prompt_vars["model_id"] = model_id
    prompt_vars["model_profile"] = model_profile
    return _ItemCtx(
        idx=idx,
        row=row,
        folder=folder,
        image_dir=image_dir,
        src_path=src_path if src_path and src_path.exists() else None,
        listing_input=listing_input,
        prompt_vars=prompt_vars,
        currency=currency,
        listing_price=listing_price,
    )


def run_generate(
    *,
    csv_path: Path,
    output_dir: Path | None = None,
    profit_rate: float | None = None,
    log: LogFn | None = None,
    should_stop: StopFn | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> Path:
    settings = get_settings()
    _log = log or (lambda m: None)
    _stop = should_stop or (lambda: False)
    _progress = on_progress or (lambda c, t, s: None)

    rows = read_products_csv(csv_path)
    if not rows:
        raise RuntimeError(f"CSV が空です: {csv_path}")

    out_root = output_dir or settings.workspace_dir / "generate"
    out_root.mkdir(parents=True, exist_ok=True)
    out_csv = out_root / "products_ready.csv"

    _log(f"入力: {csv_path} ({len(rows)} 行)")
    _log(f"出力CSV: {out_csv}")
    if profit_rate is not None:
        _log(f"利益率(今回): {profit_rate}")
    _log("モード: モデル画像のみ生成 → Engine1 フォルダへ保存 + CSV 商品コメント更新")
    _progress(0, len(rows), "開始")

    transport = (settings.chatgpt_transport or "http").strip().lower()
    use_browser = transport == "browser"
    if use_browser:
        _log("ChatGPT transport=browser（Chrome を起動します）…")
        chatgpt: ChatGPTBrowserSession | ChatGPTHttpSession = ChatGPTBrowserSession()
    else:
        _log("ChatGPT transport=http（Chrome なし・cookie HTTP）…")
        chatgpt = ChatGPTHttpSession()
    chatgpt.start()

    img_t = default_image_prompt()
    img_channel = None
    if isinstance(chatgpt, ChatGPTHttpSession):
        img_channel = chatgpt.conversation_id_from_url(settings.chatgpt_image_project_url)
        _log(f"画像 conversation_id={img_channel or 'new'}")
    else:
        assert isinstance(chatgpt, ChatGPTBrowserSession)
        chatgpt.open_project(settings.chatgpt_image_project_url)
        _log(f"画像チャンネルを開きました: {settings.chatgpt_image_project_url}")

    def _on_step(name: str, message: str) -> None:
        # Keep log light: only surface key stages.
        if name in {"image_upload", "image_generate", "image_download"}:
            _log(f"  · {name}: {message}")

    dirty = False

    def _flush_csv(*, force: bool = False) -> None:
        nonlocal dirty
        if not dirty and not force:
            return
        write_products_csv(out_csv, rows)
        try:
            if csv_path.resolve() != out_csv.resolve():
                write_products_csv(csv_path, rows)
        except Exception:  # noqa: BLE001
            pass
        dirty = False

    try:
        for idx, row in enumerate(rows):
            if _stop():
                _log("停止しました。")
                break
            if _gen_status(row) == "done":
                _log(f"スキップ (済): {row.get('商品名')}")
                _progress(idx + 1, len(rows), "スキップ")
                continue

            item = _build_item(idx, row)
            if item is None:
                row["生成ステータス"] = "failed"
                row["出品メモ"] = (row.get("出品メモ") or "") + "\nGEN_ERROR: image folder missing"
                rows[idx] = ensure_row(row)
                dirty = True
                _flush_csv(force=True)
                _log(f"失敗: 画像フォルダなし — {row.get('商品名')}")
                continue

            _log(
                f"画像+CSV ({idx + 1}/{len(rows)}): "
                f"{item.listing_input.brand_name} / {item.listing_input.product_name}"
            )
            _progress(idx, len(rows), f"生成中: {item.listing_input.product_name}")

            if item.src_path is None:
                row["生成ステータス"] = "failed"
                row["出品メモ"] = (row.get("出品メモ") or "") + "\nGEN_ERROR: source image missing"
                rows[idx] = ensure_row(row)
                dirty = True
                _flush_csv(force=True)
                _log("画像失敗: ソース画像がありません")
                continue

            # Retry once only on transient 5xx / empty asset.
            result = None
            for attempt in (1, 2):
                if use_browser:
                    assert isinstance(chatgpt, ChatGPTBrowserSession)
                    result = chatgpt.generate_image_only(
                        image_prompt=img_t,
                        output_dir=item.image_dir,
                        source_product_image=item.src_path,
                        prompt_vars=item.prompt_vars,
                        on_step=_on_step,
                        open_channel=False,
                    )
                else:
                    assert isinstance(chatgpt, ChatGPTHttpSession)
                    result = chatgpt.generate_image_only(
                        image_prompt=img_t,
                        output_dir=item.image_dir,
                        source_product_image=item.src_path,
                        prompt_vars=item.prompt_vars,
                        conversation_id=img_channel,
                        on_step=_on_step,
                    )
                if result.success or result.rate_limited:
                    break
                err = (result.error_message or "").lower()
                transient = "http 5" in err or "empty" in err or "no new" in err or "timeout" in err
                if attempt == 1 and transient:
                    _log("画像生成を再試行します（1回）…")
                    time.sleep(0.6)
                    continue
                break

            if result.rate_limited:
                row["生成ステータス"] = "rate_limited"
                rows[idx] = ensure_row(row)
                dirty = True
                _flush_csv(force=True)
                _log("レート制限のため中断しました。")
                break

            if not result.success:
                fb = _fallback_model_photo_from_source(
                    item.image_dir,
                    item.src_path,
                    brand_name=item.listing_input.brand_name,
                )
                if fb is not None:
                    _log(f"画像生成失敗のためフォールバック画像を作成: {fb}")
                    ensure_product_notice_images(item.image_dir, _log)
                row["生成ステータス"] = "failed"
                row["出品メモ"] = (row.get("出品メモ") or "") + f"\nGEN_ERROR: {result.error_message}"
                rows[idx] = ensure_row(row)
                dirty = True
                _flush_csv(force=True)
                _log(f"画像失敗: {result.error_message}")
                continue

            model_photo = _ensure_video_style_model_photo(
                item.image_dir,
                brand_name=item.listing_input.brand_name,
            )
            ensure_product_notice_images(item.image_dir, _log)

            # Lightweight persist: listing CSV beside images (skip delivery xlsx / heavy rebuild).
            updated = build_buyma_row(item.listing_input, profit_rate=profit_rate)
            merged = ensure_row(row)
            scraped_stock = str(row.get("在庫") or "").strip()
            for k, v in updated.items():
                if k in merged:
                    merged[k] = "" if v is None else str(v)
            # Never wipe Engine1 scraped stock with a weaker rebuild value.
            if scraped_stock and scraped_stock not in {"0", "1"}:
                try:
                    if int(str(merged.get("在庫") or "0") or "0") < int(scraped_stock):
                        merged["在庫"] = scraped_stock
                except ValueError:
                    merged["在庫"] = scraped_stock
            elif scraped_stock and not str(merged.get("在庫") or "").strip():
                merged["在庫"] = scraped_stock
            merged["フォルダ名"] = item.folder
            merged["ソース画像パス"] = str(item.src_path.resolve())
            merged["画像フォルダパス"] = str(item.image_dir.resolve())
            merged["通貨"] = item.currency
            merged["サイトコード"] = row.get("サイトコード") or ""
            merged["外部商品ID"] = row.get("外部商品ID") or ""
            merged["生成ステータス"] = "done"
            merged["出品結果"] = row.get("出品結果") or ""
            merged["出品URL"] = row.get("出品URL") or ""
            if item.listing_price and not merged.get("出品価格"):
                merged["出品価格"] = str(int(item.listing_price))
            merged = apply_listing_defaults(merged)
            write_listing_csv_beside_images(item.image_dir, merged)
            rows[idx] = merged
            dirty = True
            # Flush every 3 items to cut disk I/O without losing progress.
            if (idx + 1) % 3 == 0:
                _flush_csv()
            _progress(idx + 1, len(rows), "完了")
            _log(f"保存完了: {model_photo}")
    finally:
        chatgpt.close()
        _flush_csv(force=True)

    write_products_csv(out_csv, rows)
    # Sheets sync once at end (not per item).
    safe_push_csv(out_csv, log=_log)
    try:
        safe_upsert_rows([r for r in rows if (r.get("生成ステータス") or "") == "done"], log=_log)
    except Exception as exc:  # noqa: BLE001
        _log(f"Sheets更新警告: {exc}")

    # Per-batch CSV copy + update existing workbook rows (no new sheet).
    try:
        run_id = ""
        if csv_path.parent.name.startswith("run_"):
            run_id = csv_path.parent.name
        else:
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_dir = out_root / "batches"
        batch_dir.mkdir(parents=True, exist_ok=True)
        batch_csv = batch_dir / f"{run_id}_products_ready.csv"
        write_products_csv(batch_csv, rows)
        wb_path, sheet_title, n = update_workbook_rows(settings.products_workbook_path, rows)
        _log(f"バッチCSV: {batch_csv}")
        _log(f"ワークブック更新: {wb_path.name} / [{sheet_title}] ({n} 行を追加/更新)")
    except Exception as exc:  # noqa: BLE001
        _log(f"ワークブック保存警告: {exc}")

    notice_ok = 0
    for row in rows:
        folder = _resolve_image_dir(row)
        if folder and folder.is_dir() and ensure_product_notice_images(folder):
            notice_ok += 1
    _log(f"共通画像 98.png / 99.png を確認: {notice_ok} フォルダ")

    _log(f"Engine2 完了 → {out_csv}")
    _progress(len(rows), len(rows), "生成完了")
    return out_csv
