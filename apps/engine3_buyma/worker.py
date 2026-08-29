"""Engine 3 worker: CSV → Buyma list (independent of Engine 1/2)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from core.buyma.buyma_browser_service import BuymaBrowserSession
from core.buyma.buyma_listing_service import apply_listing_defaults, write_listing_csv_beside_images
from core.config import get_settings
from core.notice_images import ensure_product_notice_images
from core.csv_schema import ensure_row, read_products_csv, write_products_csv
from core.sheets.google_sheets_sync import safe_push_csv, safe_upsert_rows

LogFn = Callable[[str], None]
StopFn = Callable[[], bool]
ItemStatusFn = Callable[[int, dict, str, str], None]  # idx, row, status, error


def _resolve_production_dir(row: dict, *, generate_root: Path, idx: int) -> Path:
    """Prefer Engine1 image folder from CSV, then generate/<フォルダ名>."""
    folder_path = (row.get("画像フォルダパス") or "").strip()
    if folder_path:
        path = Path(folder_path)
        if path.is_dir():
            return path
    source = (row.get("ソース画像パス") or "").strip()
    if source:
        parent = Path(source).parent
        if parent.is_dir():
            return parent
    folder = (row.get("フォルダ名") or "").strip()
    if folder:
        candidate = generate_root / folder
        if candidate.is_dir():
            return candidate
        return candidate
    return generate_root / f"row_{idx + 1}"


def _listing_field_summary(row: dict) -> str:
    keys = ("カテゴリ", "カラー系統", "サイズ", "在庫", "買付地", "発送地", "配送方法名", "価格", "関税負担")
    parts = [f"{k}={row.get(k) or '-'}" for k in keys]
    return " / ".join(parts)


def _write_failed_queue(path: Path, rows: list[dict]) -> None:
    failed = [ensure_row(r) for r in rows if (r.get("出品結果") or "").lower() == "failed"]
    write_products_csv(path, failed)


def run_buyma_list(
    *,
    csv_path: Path,
    submit: bool = True,
    log: LogFn | None = None,
    should_stop: StopFn | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    on_item_status: ItemStatusFn | None = None,
    only_indices: list[int] | None = None,
) -> Path:
    """List products from CSV.

    Automatic end-of-batch retry is disabled. Pass ``only_indices`` to re-list
    specific failed rows (e.g. after the user clicks the red ↻ icon).
    """
    settings = get_settings()
    _log = log or (lambda m: None)
    _stop = should_stop or (lambda: False)
    _progress = on_progress or (lambda c, t, s: None)
    _item = on_item_status or (lambda i, r, s, e: None)

    rows = read_products_csv(csv_path)
    if not rows:
        raise RuntimeError(f"CSV が空です: {csv_path}")

    rows = [apply_listing_defaults(r) for r in rows]
    write_products_csv(csv_path, rows)

    out_csv = csv_path.with_name(csv_path.stem + "_listed.csv")
    failed_csv = csv_path.with_name(csv_path.stem + "_failed.csv")
    generate_root = settings.workspace_dir / "generate"

    _log(f"入力: {csv_path} ({len(rows)} 行)")
    _progress(0, len(rows), "開始")
    _log(f"結果CSV: {out_csv}")
    _log(f"失敗一時CSV: {failed_csv}")
    _log(f"自動送信: {submit}")
    if only_indices is not None:
        _log(f"単品再出品: indices={only_indices}")
    _log("Buyma ブラウザを起動します…")

    session = BuymaBrowserSession()
    session.start()
    try:
        if not session.ensure_logged_in(timeout_seconds=min(60, settings.buyma_login_timeout_seconds)):
            raise RuntimeError(
                "Buyma にログインできません。設定画面「4 設定」→「ログイン情報の保存」で cookie を登録してください。"
            )

        def _list_one(idx: int, *, pass_label: str) -> bool:
            row = apply_listing_defaults(rows[idx])
            production_dir = _resolve_production_dir(row, generate_root=generate_root, idx=idx)
            if not production_dir.is_dir():
                production_dir.mkdir(parents=True, exist_ok=True)
            write_listing_csv_beside_images(production_dir, row)
            rows[idx] = ensure_row(row)
            write_products_csv(csv_path, rows)

            _log(
                f"出品中 [{pass_label}] ({idx + 1}/{len(rows)}): "
                f"{row.get('ブランド')} / {row.get('商品名')}"
            )
            _progress(idx, len(rows), f"処理中: {row.get('商品名') or '-'}")
            _item(idx, row, "processing", "")
            _log(f"  画像フォルダ: {production_dir}")
            source_url = (row.get("仕入先URL") or "").strip()
            if source_url:
                _log(f"  仕入先URL: {source_url}")
            _log(f"  CSV項目: {_listing_field_summary(row)}")
            try:
                ensure_product_notice_images(production_dir, _log)
                ec = BuymaBrowserSession.verify_ec_product_images(production_dir)
                _log(
                    f"  EC画像チェック: {ec['count']} 件 ok={ec['ok']}"
                    + (f" warn={ec['warnings']}" if ec.get("warnings") else "")
                )
                upload_imgs = BuymaBrowserSession.collect_listing_images(production_dir)
                _log(f"  出品アップロード順: {[p.name for p in upload_imgs]}")
            except Exception as exc:  # noqa: BLE001
                _log(f"  画像チェック警告: {exc}")

            def _on_step(msg: str) -> None:
                _log(f"  · {msg}")

            result = session.list_product(production_dir, row=row, on_step=_on_step, submit=submit)
            merged = ensure_row(row)
            if result.success:
                merged["出品結果"] = "ok"
                merged["出品URL"] = result.listed_url or ""
                merged["出品エラー"] = ""
                _log("出品成功")
                rows[idx] = merged
                _item(idx, merged, "ok", "")
                _progress(idx + 1, len(rows), "完了")
                write_products_csv(out_csv, rows)
                write_products_csv(csv_path, rows)
                write_listing_csv_beside_images(production_dir, merged)
                safe_upsert_rows([merged], log=_log)
                return True

            err = result.error_message or "unknown"
            merged["出品結果"] = "failed"
            merged["出品URL"] = result.listed_url or ""
            merged["出品エラー"] = err[:500]
            _log(f"出品失敗: {err}")
            rows[idx] = merged
            _item(idx, merged, "failed", err[:200])
            write_products_csv(out_csv, rows)
            write_products_csv(csv_path, rows)
            write_listing_csv_beside_images(production_dir, merged)
            _write_failed_queue(failed_csv, rows)
            if "not logged in" in err.lower() or "cookie" in err.lower():
                raise RuntimeError(f"Buyma ログイン切れ: {err}")
            return False

        for i, row in enumerate(rows):
            st = (row.get("出品結果") or "").lower()
            if st in {"ok", "success", "done"}:
                _item(i, row, "ok", "")
            elif st == "failed":
                _item(i, row, "failed", str(row.get("出品エラー") or ""))
            else:
                _item(i, row, "pending", "")

        if only_indices is not None:
            pending = sorted({int(i) for i in only_indices if 0 <= int(i) < len(rows)})
            for idx in pending:
                rows[idx]["出品結果"] = ""
                rows[idx]["出品エラー"] = ""
            pass_label = "手動再出品"
        else:
            pending = [
                i
                for i, row in enumerate(rows)
                if (row.get("出品結果") or "").lower() not in {"ok", "success", "done"}
            ]
            pass_label = "本処理"

        for n, idx in enumerate(pending):
            if _stop():
                _log("停止しました。")
                break
            ok = _list_one(idx, pass_label=pass_label)
            if n < len(pending) - 1 and not _stop():
                if ok:
                    _log(
                        f"次の出品まで待機 "
                        f"({settings.buyma_between_items_min_seconds}-"
                        f"{settings.buyma_between_items_max_seconds}s)…"
                    )
                else:
                    _log("失敗後の短い待機…")
                session.between_items_delay()

        _write_failed_queue(failed_csv, rows)
        still_failed = sum(1 for r in rows if (r.get("出品結果") or "").lower() == "failed")
        if still_failed:
            _log(f"失敗 {still_failed} 件（赤い↻をクリックして個別再出品）→ {failed_csv}")
        else:
            _log("失敗キューは空です。")
    finally:
        session.close()

    write_products_csv(out_csv, rows)
    write_products_csv(csv_path, rows)
    _write_failed_queue(failed_csv, rows)
    safe_push_csv(out_csv, log=_log)
    _log(f"Engine3 完了 → {out_csv}")
    _progress(len(rows), len(rows), "完了")
    return out_csv
