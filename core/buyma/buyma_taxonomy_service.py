"""Buyma category/brand taxonomy helpers."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from core.paths import bundle_root

TAXONOMY_DIR = bundle_root() / "core" / "data" / "buyma_taxonomy"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load taxonomy %s: %s", path, exc)
        return {}


def load_category_map(path: Path | None = None) -> dict[str, list[str]]:
    data = _load_json(path or (TAXONOMY_DIR / "category_map.json"))
    out: dict[str, list[str]] = {}
    for key, value in data.items():
        if isinstance(value, list):
            out[str(key)] = [str(v) for v in value]
        elif isinstance(value, str):
            out[str(key)] = [p.strip() for p in value.split(">") if p.strip()]
    return out


def load_brand_aliases(path: Path | None = None) -> dict[str, str]:
    data = _load_json(path or (TAXONOMY_DIR / "brand_aliases.json"))
    return {str(k): str(v) for k, v in data.items()}


def _fold_ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")


def normalize_brand(brand: str, aliases: dict[str, str] | None = None) -> str:
    name = (brand or "").strip()
    if not name:
        return ""
    mapping = aliases if aliases is not None else load_brand_aliases()
    if name in mapping:
        return mapping[name]
    # Case-insensitive exact match
    lower = {k.lower(): v for k, v in mapping.items()}
    if name.lower() in lower:
        return lower[name.lower()]
    folded = _fold_ascii(name)
    if folded and folded.lower() in lower:
        return lower[folded.lower()]
    # Compact alphanumeric match (A.p.c. -> APC / Chloé -> chloe)
    compact = re.sub(r"[^a-z0-9]", "", folded.lower() or name.lower())
    for key, value in mapping.items():
        if re.sub(r"[^a-z0-9]", "", _fold_ascii(key).lower()) == compact:
            return value
    return folded or name


def resolve_category_path(
    category: str,
    *,
    category_map: dict[str, list[str]] | None = None,
) -> list[str]:
    """Map internal category string to Buyma path segments (prefer 3-level paths)."""
    raw = (category or "").strip()
    if not raw:
        return ["レディースファッション", "アウター", "その他アウター"]
    mapping = category_map if category_map is not None else load_category_map()
    if raw in mapping:
        return list(mapping[raw])
    # Normalize "A > B > C" / "A/B/C" into space form for map lookup.
    spaced = " ".join(p for p in re.split(r"[\s/>＞]+", raw) if p)
    if spaced in mapping:
        return list(mapping[spaced])
    # Progressive prefix match (longest first) — avoid collapsing to L1-only keys.
    candidates = sorted(mapping.keys(), key=len, reverse=True)
    for key in candidates:
        if spaced.startswith(key) or raw.startswith(key) or key in spaced:
            path = list(mapping[key])
            if len(path) >= 2 or key != "レディースファッション":
                return path
    parts = re.split(r"[\s/>＞]+", raw)
    parts = [p for p in parts if p]
    if parts:
        return parts[:4]
    return ["レディースファッション", "アウター", "その他アウター"]
