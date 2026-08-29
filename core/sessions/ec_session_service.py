"""EC site cookie / storage_state persistence (login once, reuse later)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext

from core.paths import runtime_root
from core.utils.playwright_cookies import add_cookies_resilient, cookies_are_waf_only

logger = logging.getLogger(__name__)


def secrets_root() -> Path:
    """Writable EC sessions root (always next to the .exe / repo)."""
    return runtime_root() / "secrets" / "ec_sessions"


# Back-compat alias (prefer secrets_root()).
SECRETS_ROOT = secrets_root()


def session_dir(site_code: str) -> Path:
    safe = (site_code or "site").replace("/", "_").replace("\\", "_")
    path = secrets_root() / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def storage_state_path(site_code: str) -> Path:
    return session_dir(site_code) / "storage_state.json"


def cookies_path(site_code: str) -> Path:
    return session_dir(site_code) / "cookies.json"


def _load_session_payload(site_code: str) -> tuple[Path | None, dict[str, Any] | list[Any] | None]:
    for path in (storage_state_path(site_code), cookies_path(site_code)):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        return path, data
    return None, None


def has_saved_session(site_code: str) -> bool:
    _path, data = _load_session_payload(site_code)
    if data is None:
        return False
    if isinstance(data, dict) and data.get("login_verified") is False:
        return False
    cookies = data.get("cookies") if isinstance(data, dict) else data
    if not isinstance(cookies, list) or not cookies:
        return False
    if isinstance(data, dict) and data.get("login_verified") is True:
        return True
    # Legacy files: WAF-only dumps must not count as a saved login.
    return not cookies_are_waf_only(cookies)


def clear_site_session(site_code: str, *, wipe_browser_profile: bool = False) -> list[Path]:
    """Delete storage_state.json and cookies.json for one site."""
    removed: list[Path] = []
    for path in (storage_state_path(site_code), cookies_path(site_code)):
        if path.exists():
            try:
                path.unlink()
                removed.append(path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to delete %s: %s", path, exc)
    if wipe_browser_profile:
        wiped = _wipe_ec_profile(site_code)
        if wiped:
            removed.append(wiped)
    return removed


def clear_all_sessions(*, wipe_browser_profile: bool = False) -> list[Path]:
    """Delete all saved EC session files under secrets/ec_sessions."""
    root = secrets_root()
    removed: list[Path] = []
    site_codes: list[str] = []
    if root.exists():
        for path in root.glob("*/*"):
            if path.is_file() and path.name in {"storage_state.json", "cookies.json"}:
                site_codes.append(path.parent.name)
                try:
                    path.unlink()
                    removed.append(path)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to delete %s: %s", path, exc)
        for d in list(root.glob("*")):
            if not d.is_dir():
                continue
            try:
                next(d.iterdir())
            except StopIteration:
                try:
                    d.rmdir()
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
    if wipe_browser_profile:
        for code in sorted(
            set(site_codes)
            | {"julian-fashion", "montiboutique", "minettiangeloonline", "eleonorabonucci"}
        ):
            wiped = _wipe_ec_profile(code)
            if wiped:
                removed.append(wiped)
    return removed


def _wipe_ec_profile(site_code: str) -> Path | None:
    """Remove the persistent Chrome profile so the next save starts logged-out."""
    from core.config import get_settings
    from core.utils.chrome_profile import prepare_chrome_profile, reset_profile_if_corrupt

    settings = get_settings()
    profile = Path(settings.chatgpt_profile_path).parent / f"ec-{site_code}"
    if not profile.exists():
        return None
    try:
        prepare_chrome_profile(profile)
        reset_profile_if_corrupt(profile)
        logger.info("Wiped EC Chrome profile %s", profile)
        return profile
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to wipe EC profile %s: %s", profile, exc)
        return None


def save_http_cookies(
    site_code: str,
    cookies: list[dict[str, Any]],
    *,
    login_verified: bool = True,
) -> Path:
    """Persist HTTP-obtained cookies as Playwright storage_state + cookies.json."""
    cleaned = [item for item in cookies if isinstance(item, dict) and item.get("name")]
    if not cleaned:
        raise ValueError(f"No cookies to save for {site_code}")
    path = storage_state_path(site_code)
    state = {
        "cookies": cleaned,
        "origins": [],
        "login_verified": bool(login_verified),
    }
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    payload: dict[str, Any] = {
        "version": 1,
        "source": "http",
        "cookie_count": len(cleaned),
        "cookies": cleaned,
        "login_verified": bool(login_verified),
    }
    cookies_path(site_code).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "Saved HTTP EC session for %s (%s cookies, verified=%s) -> %s",
        site_code,
        len(cleaned),
        login_verified,
        path,
    )
    return path


def save_storage_state(
    context: BrowserContext,
    site_code: str,
    *,
    login_verified: bool | None = None,
) -> Path:
    path = storage_state_path(site_code)
    context.storage_state(path=str(path))
    cookies = list(context.cookies())
    payload: dict[str, Any] = {
        "version": 1,
        "cookie_count": len(cookies),
        "cookies": cookies,
    }
    if login_verified is not None:
        payload["login_verified"] = bool(login_verified)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            raw = None
        if isinstance(raw, dict):
            raw["login_verified"] = bool(login_verified)
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    cookies_path(site_code).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "Saved EC session for %s (%s cookies, verified=%s) -> %s",
        site_code,
        len(cookies),
        login_verified,
        path,
    )
    return path


def load_cookies_for_httpx(site_code: str) -> dict[str, str]:
    """Return name->value cookie map for httpx."""
    path = cookies_path(site_code)
    if not path.exists():
        path = storage_state_path(site_code)
        if not path.exists():
            return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    cookies = data.get("cookies") if isinstance(data, dict) else data
    if not isinstance(cookies, list):
        return {}
    out: dict[str, str] = {}
    for item in cookies:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if name and value is not None:
            out[str(name)] = str(value)
    return out


def apply_saved_cookies(context: BrowserContext, site_code: str) -> int:
    """Apply cookies from storage_state.json / cookies.json into an open context."""
    path = storage_state_path(site_code)
    if not path.exists():
        path = cookies_path(site_code)
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read EC session for %s: %s", site_code, exc)
        return 0
    cookies = data.get("cookies") if isinstance(data, dict) else data
    if not isinstance(cookies, list) or not cookies:
        return 0
    applied = add_cookies_resilient(context, [item for item in cookies if isinstance(item, dict)])
    if applied:
        logger.info("Applied %s EC cookies for %s", applied, site_code)
    return applied


def cookie_header(site_code: str) -> str:
    mapping = load_cookies_for_httpx(site_code)
    return "; ".join(f"{k}={v}" for k, v in mapping.items())
