"""Buyma cookie load/save (dedicated secrets file; do not share with ChatGPT)."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext

from core.paths import runtime_root
from core.utils.playwright_cookies import add_cookies_resilient

logger = logging.getLogger(__name__)

DEFAULT_COOKIE_PATH = runtime_root() / "secrets" / "buyma_cookies.json"
BUYMA_DOMAINS = (".buyma.com", "buyma.com", "www.buyma.com")


def cookie_file_path(explicit: Path | str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    return DEFAULT_COOKIE_PATH


def load_cookies_from_file(path: Path | str | None = None) -> list[dict[str, Any]]:
    file_path = cookie_file_path(path)
    if not file_path.exists():
        return []
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read Buyma cookie file %s: %s", file_path, exc)
        return []

    if isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
        cookies = raw["cookies"]
    elif isinstance(raw, list):
        cookies = raw
    else:
        return []

    normalized: list[dict[str, Any]] = []
    for item in cookies:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if not name or value is None:
            continue
        domain = str(item.get("domain") or ".buyma.com")
        cookie: dict[str, Any] = {
            "name": str(name),
            "value": str(value),
            "domain": domain,
            "path": str(item.get("path") or "/"),
        }
        if "expires" in item and item["expires"] not in (None, ""):
            try:
                cookie["expires"] = float(item["expires"])
            except (TypeError, ValueError):
                pass
        if "expirationDate" in item and "expires" not in cookie:
            try:
                cookie["expires"] = float(item["expirationDate"])
            except (TypeError, ValueError):
                pass
        if "httpOnly" in item:
            cookie["httpOnly"] = bool(item["httpOnly"])
        if "secure" in item:
            cookie["secure"] = bool(item["secure"])
        same_site = item.get("sameSite") or item.get("same_site")
        if same_site:
            mapping = {
                "no_restriction": "None",
                "unspecified": "Lax",
                "lax": "Lax",
                "strict": "Strict",
                "none": "None",
            }
            cookie["sameSite"] = mapping.get(str(same_site).lower(), str(same_site))
        normalized.append(cookie)
    return normalized


def save_cookie_list(
    cookies: list[dict[str, Any]],
    path: Path | str | None = None,
    *,
    source: str = "http",
) -> dict[str, Any]:
    """Persist a cookie list (HTTP or Playwright) to buyma_cookies.json."""
    file_path = cookie_file_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = [item for item in cookies if isinstance(item, dict) and item.get("name")]
    buyma_only = [item for item in cleaned if "buyma.com" in str(item.get("domain") or "")]
    if buyma_only:
        cleaned = buyma_only
    if file_path.exists() and cleaned:
        bak = file_path.with_suffix(".bak.json")
        try:
            shutil.copy2(file_path, bak)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to backup Buyma cookies: %s", exc)
    payload = {
        "version": 1,
        "source": source,
        "cookie_count": len(cleaned),
        "cookies": cleaned,
    }
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(file_path), "cookie_count": len(cleaned), "skipped": False}


def save_cookies_to_file(context: BrowserContext, path: Path | str | None = None) -> dict[str, Any]:
    all_cookies = list(context.cookies())
    return save_cookie_list(all_cookies, path, source="playwright")


def clear_buyma_session(*, wipe_browser_profile: bool = False) -> list[Path]:
    """Delete Buyma cookie files and optionally the Chrome profile."""
    from core.config import get_settings
    from core.utils.chrome_profile import prepare_chrome_profile, reset_profile_if_corrupt

    settings = get_settings()
    removed: list[Path] = []
    path = Path(settings.buyma_cookies_path)
    for candidate in (path, path.with_suffix(".bak.json")):
        if candidate.exists():
            try:
                candidate.unlink()
                removed.append(candidate)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to delete %s: %s", candidate, exc)
    if wipe_browser_profile:
        profile = Path(settings.buyma_profile_path)
        if profile.exists():
            try:
                prepare_chrome_profile(profile)
                reset_profile_if_corrupt(profile)
                removed.append(profile)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to wipe Buyma profile %s: %s", profile, exc)
    return removed


def apply_cookies(context: BrowserContext, path: Path | str | None = None) -> int:
    cookies = load_cookies_from_file(path)
    if not cookies:
        return 0
    try:
        return add_cookies_resilient(context, cookies)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to apply Buyma cookies: %s", exc)
        return 0
