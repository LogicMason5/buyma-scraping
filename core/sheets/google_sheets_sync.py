"""Google Sheets sync for the final products CSV schema (local CSV is source of truth)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable

from core.config import get_settings
from core.csv_schema import ROW_KEYS, ensure_row, read_products_csv, write_products_csv

logger = logging.getLogger(__name__)


class SheetsSyncError(RuntimeError):
    pass


def sheets_enabled() -> bool:
    settings = get_settings()
    return bool(settings.google_sheets_enabled and (settings.google_sheets_spreadsheet_id or "").strip())


def _require_gspread():
    try:
        import gspread  # noqa: F401
        from google.oauth2.service_account import Credentials  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise SheetsSyncError(
            "gspread / google-auth が未インストールです。py -3 -m pip install gspread google-auth"
        ) from exc


def _client():
    _require_gspread()
    import gspread
    from google.oauth2.service_account import Credentials

    settings = get_settings()
    cred_path = Path(settings.google_service_account_json)
    if not cred_path.exists():
        raise SheetsSyncError(f"サービスアカウントJSONがありません: {cred_path}")
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(str(cred_path), scopes=scopes)
    return gspread.authorize(creds)


def _open_worksheet():
    settings = get_settings()
    if not sheets_enabled():
        raise SheetsSyncError("GOOGLE_SHEETS_ENABLED が無効、または SPREADSHEET_ID が未設定です")
    gc = _client()
    sh = gc.open_by_key(settings.google_sheets_spreadsheet_id.strip())
    title = (settings.google_sheets_worksheet or "products").strip() or "products"
    try:
        return sh.worksheet(title)
    except Exception:  # noqa: BLE001
        return sh.add_worksheet(title=title, rows=2000, cols=max(26, len(ROW_KEYS)))


def _row_values(row: dict[str, Any]) -> list[str]:
    ready = ensure_row(row)
    return [ready.get(k, "") for k in ROW_KEYS]


def _ensure_header(ws) -> None:
    values = ws.row_values(1)
    if values != list(ROW_KEYS):
        ws.resize(rows=max(ws.row_count, 2), cols=max(ws.col_count, len(ROW_KEYS)))
        ws.update("A1", [list(ROW_KEYS)], value_input_option="RAW")


def _key_of(row: dict[str, Any]) -> str:
    folder = (row.get("フォルダ名") or "").strip()
    if folder:
        return f"folder:{folder}"
    ext = (row.get("外部商品ID") or "").strip()
    if ext:
        return f"ext:{ext}"
    return ""


def push_csv_to_sheet(csv_path: Path | str) -> int:
    """Replace worksheet contents with local CSV (header + all rows)."""
    if not sheets_enabled():
        logger.info("Sheets sync skipped (disabled)")
        return 0
    rows = read_products_csv(csv_path)
    ws = _open_worksheet()
    _ensure_header(ws)
    body = [_row_values(r) for r in rows]
    # Clear data rows then write
    if ws.row_count > 1:
        ws.delete_rows(2, ws.row_count)
    if body:
        ws.append_rows(body, value_input_option="RAW")
    logger.info("Pushed %s rows to Google Sheet from %s", len(body), csv_path)
    return len(body)


def pull_sheet_to_csv(csv_path: Path | str) -> int:
    """Overwrite local CSV from worksheet (manual edits)."""
    if not sheets_enabled():
        logger.info("Sheets pull skipped (disabled)")
        return 0
    ws = _open_worksheet()
    _ensure_header(ws)
    raw = ws.get_all_records(expected_headers=list(ROW_KEYS))
    rows = [ensure_row(r) for r in raw]
    write_products_csv(csv_path, rows)
    logger.info("Pulled %s rows from Google Sheet to %s", len(rows), csv_path)
    return len(rows)


def upsert_rows_by_folder(rows: Iterable[dict[str, Any]]) -> int:
    """Insert or update sheet rows keyed by フォルダ名 (fallback: 外部商品ID)."""
    if not sheets_enabled():
        logger.info("Sheets upsert skipped (disabled)")
        return 0
    payload = [ensure_row(r) for r in rows if _key_of(r)]
    if not payload:
        return 0
    ws = _open_worksheet()
    _ensure_header(ws)
    existing = ws.get_all_values()
    # Map key -> 1-based sheet row index
    index: dict[str, int] = {}
    if len(existing) >= 2:
        headers = existing[0]
        try:
            folder_i = headers.index("フォルダ名")
        except ValueError:
            folder_i = 0
        try:
            ext_i = headers.index("外部商品ID")
        except ValueError:
            ext_i = -1
        for i, line in enumerate(existing[1:], start=2):
            folder = line[folder_i].strip() if folder_i < len(line) else ""
            ext = line[ext_i].strip() if ext_i >= 0 and ext_i < len(line) else ""
            if folder:
                index[f"folder:{folder}"] = i
            elif ext:
                index[f"ext:{ext}"] = i

    updated = 0
    for row in payload:
        key = _key_of(row)
        values = _row_values(row)
        if key in index:
            sheet_row = index[key]
            end_col = _col_letter(len(ROW_KEYS))
            ws.update(f"A{sheet_row}:{end_col}{sheet_row}", [values], value_input_option="RAW")
        else:
            ws.append_row(values, value_input_option="RAW")
            # approximate new index for subsequent same-key updates in this batch
            index[key] = (ws.row_count if hasattr(ws, "row_count") else 0) or (len(existing) + 1 + updated)
        updated += 1
    logger.info("Upserted %s rows to Google Sheet", updated)
    return updated


def _col_letter(n: int) -> str:
    """1-based column index to A1 letter(s)."""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s or "A"


def safe_upsert_rows(rows: Iterable[dict[str, Any]], *, log=None) -> int:
    """Best-effort upsert; never raises into engine workers."""
    _log = log or (lambda m: None)
    try:
        return upsert_rows_by_folder(rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Google Sheets upsert failed: %s", exc)
        _log(f"Sheets同期警告: {exc}")
        return 0


def safe_push_csv(csv_path: Path | str, *, log=None) -> int:
    _log = log or (lambda m: None)
    try:
        return push_csv_to_sheet(csv_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Google Sheets push failed: %s", exc)
        _log(f"Sheets同期警告: {exc}")
        return 0
