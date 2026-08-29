"""Shared Buyma CSV schema (progressive fill across 3 independent engines)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

# Base listing columns (same as legacy buyma_listing.csv).
BUYMA_CSV_HEADERS = [
    "フォルダ名",
    "商品名",
    "ブランド",
    "モデル・ライン",
    "カテゴリ",
    "商品コメント",
    "色・サイズ補足情報",
    "購入期限",
    "仕入先URL",
    "買付地",
    "ショップ名",
    "発送地",
    "仕入先保存",
    "カラー系統",
    "色",
    "サイズ",
    "シーズン",
    "タグ",
    "テーマ",
    "価格",
    "参考価格",
    "配送方法名",
    "在庫",
    "型番/メモ",
    "関税負担",
    "出品メモ",
    "ライバル価格",
    "ライバルURL",
    "SKU",
    "価格計算式",
    "品代(海外仕入)",
    "送料(仕入)",
    "梱包",
    "送料(出品)",
    "出品価格",
    "関税(10%)",
    "消費税(10%)",
    "仕入合計",
    "BUYMA手数料",
    "利益%",
    "出品価格_再掲",
    "利益",
]

# Engine pipeline metadata columns (appended).
ENGINE_EXTRA_HEADERS = [
    "ソース画像パス",
    "画像フォルダパス",
    "通貨",
    "サイトコード",
    "外部商品ID",
    "生成ステータス",
    "出品結果",
    "出品URL",
    "出品エラー",
    "在庫監視",
    "Buyma公開",
]

ALL_HEADERS = BUYMA_CSV_HEADERS + ENGINE_EXTRA_HEADERS

# Unique internal keys when writing (avoid duplicate 出品価格 header issues).
ROW_KEYS = list(ALL_HEADERS)


def empty_row() -> dict[str, str]:
    return {k: "" for k in ROW_KEYS}


def ensure_row(data: dict[str, Any] | None = None) -> dict[str, str]:
    row = empty_row()
    if not data:
        return row
    for key, value in data.items():
        if key in row:
            row[key] = "" if value is None else str(value)
        elif key == "出品価格" and not row.get("出品価格"):
            row["出品価格"] = "" if value is None else str(value)
    return row


def _is_excel_workbook(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm", ".xls", ".xltx", ".xltm"}:
        return True
    try:
        with path.open("rb") as f:
            magic = f.read(8)
        # ZIP (xlsx) or OLE Compound (xls)
        return magic.startswith(b"PK\x03\x04") or magic.startswith(b"\xd0\xcf\x11\xe0")
    except Exception:  # noqa: BLE001
        return False


def _read_csv_rows(file_path: Path, encoding: str) -> list[dict[str, str]]:
    with file_path.open("r", encoding=encoding, newline="") as f:
        reader = csv.DictReader(f)
        rows: list[dict[str, str]] = []
        for raw in reader:
            normalized = dict(raw)
            rows.append(ensure_row(normalized))
        return rows


def read_products_csv(path: Path | str) -> list[dict[str, str]]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    if _is_excel_workbook(file_path):
        raise ValueError(
            f"Excelファイルは直接読めません: {file_path.name}\n"
            "「シート→CSV」または「前日シート」でCSVを出力してから出品してください。"
        )
    last_err: Exception | None = None
    for encoding in ("utf-8-sig", "cp932", "utf-8"):
        try:
            return _read_csv_rows(file_path, encoding)
        except UnicodeDecodeError as exc:
            last_err = exc
            continue
    if last_err is not None:
        raise ValueError(f"CSVの文字コードを判別できません: {file_path.name}") from last_err
    return []


def write_products_csv(path: Path | str, rows: list[dict[str, Any]]) -> Path:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ROW_KEYS, extrasaction="ignore")
        writer.writeheader()
        for raw in rows:
            writer.writerow(ensure_row(raw))
    return file_path


def append_product_row(path: Path | str, row: dict[str, Any]) -> Path:
    """Append one row; create file with header if missing. Flushes immediately."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    exists = file_path.exists() and file_path.stat().st_size > 0
    with file_path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ROW_KEYS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(ensure_row(row))
        f.flush()
    return file_path


def write_empty_products_csv(path: Path | str) -> Path:
    """Create a header-only CSV in the final ALL_HEADERS format."""
    return write_products_csv(path, [])


def products_template_path() -> Path:
    """Bundled empty template (header only)."""
    from core.paths import bundle_root

    return bundle_root() / "templates" / "products_template.csv"
