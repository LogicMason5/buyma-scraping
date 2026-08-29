"""Per-EC-site email/password (shared EC_SITE_* remains a fallback)."""

from __future__ import annotations

from typing import Any

# site_code -> (email env key, password env key)
SITE_CREDENTIAL_KEYS: dict[str, tuple[str, str]] = {
    "julian-fashion": ("EC_JULIAN_EMAIL", "EC_JULIAN_PASSWORD"),
    "montiboutique": ("EC_MONTI_EMAIL", "EC_MONTI_PASSWORD"),
    "minettiangeloonline": ("EC_MINETTI_EMAIL", "EC_MINETTI_PASSWORD"),
    "eleonorabonucci": ("EC_ELEONORA_EMAIL", "EC_ELEONORA_PASSWORD"),
}


def _env_to_field(env_key: str) -> str:
    return env_key.lower()


def resolve_site_account(settings: Any, site_code: str) -> tuple[str, str]:
    """Return (email, password) for one EC site, with shared-account fallback."""
    shared_email = str(getattr(settings, "ec_site_email", "") or "").strip()
    shared_password = str(getattr(settings, "ec_site_password", "") or "").strip()
    keys = SITE_CREDENTIAL_KEYS.get(site_code)
    if not keys:
        return shared_email, shared_password
    email_key, pass_key = keys
    email = str(getattr(settings, _env_to_field(email_key), "") or "").strip() or shared_email
    password = str(getattr(settings, _env_to_field(pass_key), "") or "").strip()
    if not password:
        if site_code == "minettiangeloonline":
            password = str(getattr(settings, "ec_minetti_password", "") or "").strip() or shared_password
        else:
            password = shared_password
    return email, password


def load_all_site_accounts(env: dict[str, str]) -> dict[str, dict[str, str]]:
    """Build per-site credentials from a .env key map."""
    shared_email = (env.get("EC_SITE_EMAIL") or "").strip()
    shared_password = (env.get("EC_SITE_PASSWORD") or "").strip()
    out: dict[str, dict[str, str]] = {}
    for code, (email_key, pass_key) in SITE_CREDENTIAL_KEYS.items():
        email = (env.get(email_key) or "").strip() or shared_email
        password = (env.get(pass_key) or "").strip()
        if not password:
            if code == "minettiangeloonline":
                password = (env.get("EC_MINETTI_PASSWORD") or "").strip() or shared_password
            else:
                password = shared_password
        out[code] = {"email": email, "password": password}
    return out


def env_updates_from_site_accounts(accounts: dict[str, dict[str, str]]) -> dict[str, str]:
    """Flatten in-memory per-site accounts into .env updates."""
    updates: dict[str, str] = {}
    for code, (email_key, pass_key) in SITE_CREDENTIAL_KEYS.items():
        slot = accounts.get(code) or {}
        updates[email_key] = str(slot.get("email") or "").strip()
        updates[pass_key] = str(slot.get("password") or "").strip()
    # Keep legacy keys in sync with Julian (most common shared login).
    julian = accounts.get("julian-fashion") or {}
    updates["EC_SITE_EMAIL"] = str(julian.get("email") or "").strip()
    updates["EC_SITE_PASSWORD"] = str(julian.get("password") or "").strip()
    minetti = accounts.get("minettiangeloonline") or {}
    updates["EC_MINETTI_PASSWORD"] = str(minetti.get("password") or "").strip()
    return updates
