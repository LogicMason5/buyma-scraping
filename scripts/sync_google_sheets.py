"""Push / pull products CSV <-> Google Sheets."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import clear_settings_cache
from core.sheets.google_sheets_sync import pull_sheet_to_csv, push_csv_to_sheet, sheets_enabled


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync products CSV with Google Sheets")
    parser.add_argument("action", choices=("push", "pull"), help="push=CSV→Sheet, pull=Sheet→CSV")
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=str(ROOT / "workspace" / "generate" / "products_ready.csv"),
        help="Local CSV path",
    )
    args = parser.parse_args()
    clear_settings_cache()
    if not sheets_enabled():
        raise SystemExit(
            "Sheets sync is disabled. Set GOOGLE_SHEETS_ENABLED=true and "
            "GOOGLE_SHEETS_SPREADSHEET_ID in .env, then share the sheet with the service account."
        )
    path = Path(args.csv_path)
    if args.action == "push":
        n = push_csv_to_sheet(path)
        print(f"Pushed {n} rows → Google Sheet from {path}")
    else:
        n = pull_sheet_to_csv(path)
        print(f"Pulled {n} rows → {path}")


if __name__ == "__main__":
    main()
