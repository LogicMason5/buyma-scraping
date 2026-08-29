"""Helpers for brand / category filtering during scrape."""

from __future__ import annotations

import re


def _split_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[,、/\n|;]+", str(raw))
    return [p.strip() for p in parts if p.strip()]


def matches_brand_filter(brand: str | None, name: str | None, url: str | None, brand_filter: str | None) -> bool:
    keys = _split_keywords(brand_filter)
    if not keys:
        return True
    hay = " ".join([brand or "", name or "", url or ""]).lower()
    return any(k.lower() in hay for k in keys)


def matches_category_filter(category: str | None, url: str | None, name: str | None, category_filter: str | None) -> bool:
    keys = _split_keywords(category_filter)
    if not keys:
        return True
    hay = " ".join([category or "", url or "", name or ""]).lower()
    return any(k.lower() in hay for k in keys)


def card_matches_filters(
    card: dict,
    *,
    brand_filter: str | None = None,
    category_filter: str | None = None,
) -> bool:
    brand = card.get("brand") or ""
    name = card.get("name") or ""
    href = card.get("href") or ""
    category = card.get("category") or ""
    return matches_brand_filter(brand, name, href, brand_filter) and matches_category_filter(
        category, href, name, category_filter
    )
