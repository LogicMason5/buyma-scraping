"""Create an empty products CSV in the final ALL_HEADERS format."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.csv_schema import ROW_KEYS, write_empty_products_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Create empty products CSV (final schema headers only).")
    parser.add_argument(
        "path",
        nargs="?",
        default=str(ROOT / "workspace" / "generate" / "products_empty.csv"),
        help="Output CSV path (default: workspace/generate/products_empty.csv)",
    )
    args = parser.parse_args()
    out = write_empty_products_csv(args.path)
    print(f"Created empty CSV ({len(ROW_KEYS)} columns): {out.resolve()}")


if __name__ == "__main__":
    main()
