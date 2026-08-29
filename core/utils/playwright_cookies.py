"""Sanitize Playwright cookies so add_cookies does not fail the whole batch."""

from __future__ import annotations

import logging
from typing import Any

from playwright.sync_api import BrowserContext

logger = logging.getLogger(__name__)

_SAMESITE = {
    "no_restriction": "None",
    "unspecified": "Lax",
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
}

WAF_NAME_MARKERS = ("azwaf", "cf_clearance", "__cf_bm", "__cfwaitingroom")


def is_waf_cookie_name(name: str) -> bool:
    lowered = (name or "").lower()
    return any(marker in lowered for marker in WAF_NAME_MARKERS)


def cookies_are_waf_only(cookies: list[Any]) -> bool:
    names = [
        str(item.get("name") or "")
        for item in cookies
        if isinstance(item, dict)
    ]
    meaningful = [name for name in names if name and not is_waf_cookie_name(name)]
    return not meaningful


def sanitize_playwright_cookie(item: dict[str, Any]) -> dict[str, Any] | None:
    """Return a Playwright-safe cookie dict, or None if it cannot be applied."""
    name = item.get("name")
    value = item.get("value")
    domain = str(item.get("domain") or "").strip()
    if not name or value is None or not domain:
        return None
    cookie: dict[str, Any] = {
        "name": str(name),
        "value": str(value),
        "domain": domain,
        "path": str(item.get("path") or "/"),
    }
    expires = item.get("expires")
    if expires not in (None, "", -1, "-1"):
        try:
            cookie["expires"] = float(expires)
        except (TypeError, ValueError):
            pass
    elif item.get("expirationDate") not in (None, ""):
        try:
            cookie["expires"] = float(item["expirationDate"])
        except (TypeError, ValueError):
            pass
    if "httpOnly" in item:
        cookie["httpOnly"] = bool(item["httpOnly"])
    if "secure" in item:
        cookie["secure"] = bool(item["secure"])
    same = item.get("sameSite") or item.get("same_site")
    if same not in (None, ""):
        mapped = _SAMESITE.get(str(same).lower(), str(same))
        if mapped in {"Lax", "None", "Strict"}:
            cookie["sameSite"] = mapped
    return cookie


def add_cookies_resilient(context: BrowserContext, cookies: list[dict[str, Any]]) -> int:
    """Apply cookies; skip invalid ones instead of failing the whole set."""
    cleaned = [c for c in (sanitize_playwright_cookie(item) for item in cookies) if c]
    if not cleaned:
        return 0
    try:
        context.add_cookies(cleaned)
        return len(cleaned)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Batch add_cookies failed (%s); applying one by one", exc)
    applied = 0
    for cookie in cleaned:
        try:
            context.add_cookies([cookie])
            applied += 1
        except Exception as inner:  # noqa: BLE001
            logger.warning("Skipped cookie %s: %s", cookie.get("name"), inner)
    return applied


def clear_waf_cookies(context: BrowserContext) -> int:
    """Drop Azure/Cloudflare challenge cookies so a headed login can complete."""
    removed = 0
    try:
        current = list(context.cookies())
    except Exception:  # noqa: BLE001
        return 0
    for item in current:
        name = str(item.get("name") or "")
        if not is_waf_cookie_name(name):
            continue
        try:
            context.clear_cookies(name=name)
            removed += 1
        except Exception:  # noqa: BLE001
            continue
    return removed
