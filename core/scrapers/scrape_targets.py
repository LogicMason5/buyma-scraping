"""Persist scrape brand/category selections (per EC site)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.paths import runtime_root
from core.scrapers.site_catalog import all_site_codes


def scrape_targets_path() -> Path:
    path = runtime_root() / "secrets" / "scrape_targets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _empty() -> dict[str, dict[str, list[str]]]:
    return {code: {"brands": [], "categories": []} for code in all_site_codes()}


def load_scrape_targets() -> dict[str, dict[str, list[str]]]:
    path = scrape_targets_path()
    data = _empty()
    if not path.exists():
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return data
    if not isinstance(raw, dict):
        return data
    for code in all_site_codes():
        block = raw.get(code) or {}
        if not isinstance(block, dict):
            continue
        brands = block.get("brands") or []
        cats = block.get("categories") or []
        data[code] = {
            "brands": [str(x).strip() for x in brands if str(x).strip()],
            "categories": [str(x).strip() for x in cats if str(x).strip()],
        }
    return data


def save_scrape_targets(data: dict[str, Any]) -> Path:
    path = scrape_targets_path()
    cleaned = _empty()
    for code in all_site_codes():
        block = data.get(code) or {}
        brands = block.get("brands") or []
        cats = block.get("categories") or []
        cleaned[code] = {
            "brands": [str(x).strip() for x in brands if str(x).strip()],
            "categories": [str(x).strip() for x in cats if str(x).strip()],
        }
    path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def filters_for_site(site_code: str, targets: dict[str, dict[str, list[str]]] | None = None) -> tuple[str | None, str | None]:
    """Return (brand_filter, category_filter) comma strings for one site."""
    data = targets if targets is not None else load_scrape_targets()
    block = data.get(site_code) or {}
    brands = block.get("brands") or []
    cats = block.get("categories") or []
    brand_filter = ", ".join(brands) if brands else None
    category_filter = ", ".join(cats) if cats else None
    return brand_filter, category_filter


def summarize_targets(targets: dict[str, dict[str, list[str]]] | None = None) -> str:
    data = targets if targets is not None else load_scrape_targets()
    parts: list[str] = []
    for code, block in data.items():
        b = len(block.get("brands") or [])
        c = len(block.get("categories") or [])
        if b or c:
            parts.append(f"{code}: ブランド{b}/カテゴリ{c}")
    return " / ".join(parts) if parts else "未設定（全件対象）"
