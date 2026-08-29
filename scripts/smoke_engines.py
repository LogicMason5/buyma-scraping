"""Offline smoke for 3-engine desktop layout (no browser required)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.buyma.buyma_taxonomy_service import normalize_brand  # noqa: E402
from core.config import clear_settings_cache, get_settings  # noqa: E402
from core.csv_schema import ensure_row, read_products_csv, write_products_csv  # noqa: E402
from core.scrapers.sites import SCRAPER_REGISTRY  # noqa: E402


def main() -> int:
    clear_settings_cache()
    s = get_settings()
    assert s.chatgpt_cookies_path.exists(), f"missing chatgpt cookies: {s.chatgpt_cookies_path}"
    assert (s.secrets_dir / "ec_sessions").exists(), "missing ec_sessions"
    assert list(SCRAPER_REGISTRY), "no scrapers"
    assert normalize_brand("A.p.c.") == "A.P.C."

    # Build a 1-row CSV from an existing generate folder if present
    gen = s.workspace_dir / "generate"
    folders = [
        p for p in gen.iterdir() if p.is_dir() and ((p / "buyma_listing.csv").exists() or (p / "description.txt").exists())
    ] if gen.exists() else []
    smoke_csv = s.workspace_dir / "buyma" / "smoke_one.csv"
    if folders:
        sample = sorted(folders, key=lambda p: p.stat().st_mtime, reverse=True)[0]
        legacy = sample / "buyma_listing.csv"
        if legacy.exists():
            rows = read_products_csv(legacy)
            if rows:
                row = ensure_row(rows[0])
                row["フォルダ名"] = sample.name
                row["生成ステータス"] = "done"
                write_products_csv(smoke_csv, [row])
                print(f"smoke_csv={smoke_csv}")
        else:
            write_products_csv(smoke_csv, [ensure_row({"フォルダ名": sample.name, "生成ステータス": "done"})])
            print(f"smoke_csv_minimal={smoke_csv}")
    else:
        print("no_generate_folders")

    # Confirm old stacks gone
    assert not (ROOT / "frontend").exists(), "frontend still present"
    assert not (ROOT / "backend").exists(), "backend still present"
    assert not (ROOT / "docker-compose.yml").exists(), "docker-compose still present"

    print("cookies_ok")
    print("scrapers_ok", len(SCRAPER_REGISTRY))
    print("smoke_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
