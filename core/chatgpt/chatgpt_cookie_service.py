from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext

from core.paths import runtime_root

logger = logging.getLogger(__name__)

DEFAULT_COOKIE_PATH = runtime_root() / "secrets" / "chatgpt_cookies.json"
REQUIRED_SESSION_NAMES = (
    "__Secure-next-auth.session-token",
    "__Secure-next-auth.session-token.0",
)


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
        logger.warning("Failed to read cookie file %s: %s", file_path, exc)
        return []

    if isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
        cookies = raw["cookies"]
    elif isinstance(raw, list):
        cookies = raw
    else:
        logger.warning("Unsupported cookie JSON format in %s", file_path)
        return []

    normalized: list[dict[str, Any]] = []
    for item in cookies:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if not name or value is None:
            continue
        cookie: dict[str, Any] = {
            "name": str(name),
            "value": str(value),
            "domain": str(item.get("domain") or ".chatgpt.com"),
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


def _has_session_token(cookies: list[dict[str, Any]]) -> bool:
    names = {str(c.get("name") or "") for c in cookies}
    if any(n in names for n in REQUIRED_SESSION_NAMES):
        return True
    return any("session-token" in n for n in names)


def save_cookies_to_file(context: BrowserContext, path: Path | str | None = None) -> dict[str, Any]:
    """Save cookies, but never overwrite a richer/valid file with a thinner session."""
    file_path = cookie_file_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    cookies = list(context.cookies())
    existing = load_cookies_from_file(file_path)
    existing_count = len(existing)
    new_count = len(cookies)
    new_has_session = _has_session_token(cookies)
    existing_has_session = _has_session_token(existing)

    # Guard: do not replace a good export with a partial capture.
    if existing_count > 0:
        if existing_has_session and not new_has_session:
            logger.warning(
                "Skip cookie overwrite: new session missing token (existing=%s new=%s)",
                existing_count,
                new_count,
            )
            return {
                "path": str(file_path),
                "cookie_count": existing_count,
                "cookie_names": sorted({c.get("name", "") for c in existing if c.get("name")}),
                "skipped": True,
                "reason": "missing_session_token",
            }
        if new_count + 5 < existing_count and existing_has_session:
            logger.warning(
                "Skip cookie overwrite: new count too low (existing=%s new=%s)",
                existing_count,
                new_count,
            )
            return {
                "path": str(file_path),
                "cookie_count": existing_count,
                "cookie_names": sorted({c.get("name", "") for c in existing if c.get("name")}),
                "skipped": True,
                "reason": "thinner_session",
            }

    if file_path.exists() and new_count > 0:
        bak = file_path.with_suffix(".bak.json")
        try:
            shutil.copy2(file_path, bak)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to backup cookies: %s", exc)

    payload = {
        "version": 1,
        "source": "playwright",
        "cookie_count": new_count,
        "cookies": cookies,
    }
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    names = sorted({c.get("name", "") for c in cookies if c.get("name")})
    return {
        "path": str(file_path),
        "cookie_count": new_count,
        "cookie_names": names,
        "skipped": False,
    }


def has_saved_chatgpt_session(path: Path | str | None = None) -> bool:
    """True when chatgpt_cookies.json has a next-auth session token."""
    file_path = cookie_file_path(path)
    if not file_path.exists() or file_path.stat().st_size < 10:
        return False
    return _has_session_token(load_cookies_from_file(file_path))


def clear_chatgpt_session(
    *,
    wipe_browser_profile: bool = False,
    cookies_path: Path | str | None = None,
) -> list[Path]:
    """Delete ChatGPT cookie files and optionally the Chrome profile."""
    from core.config import get_settings
    from core.utils.chrome_profile import prepare_chrome_profile, reset_profile_if_corrupt

    settings = get_settings()
    removed: list[Path] = []
    path = cookie_file_path(cookies_path or settings.chatgpt_cookies_path)
    for candidate in (path, path.with_suffix(".bak.json")):
        if candidate.exists():
            try:
                candidate.unlink()
                removed.append(candidate)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to delete %s: %s", candidate, exc)
    if wipe_browser_profile:
        profile = Path(settings.chatgpt_profile_path)
        if profile.exists():
            try:
                prepare_chrome_profile(profile)
                reset_profile_if_corrupt(profile)
                removed.append(profile)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to wipe ChatGPT profile %s: %s", profile, exc)
    return removed


def apply_cookies(context: BrowserContext, path: Path | str | None = None) -> int:
    cookies = load_cookies_from_file(path)
    if not cookies:
        return 0
    try:
        context.add_cookies(cookies)
        return len(cookies)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to apply cookies: %s", exc)
        return 0
