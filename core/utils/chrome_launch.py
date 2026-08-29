"""Shared Chrome / Playwright launch settings and Azure WAF wait helpers."""

from __future__ import annotations

import logging
import time
from typing import Any

from playwright.sync_api import BrowserContext, Page

logger = logging.getLogger(__name__)

WAF_URL_MARKERS = (
    "afd_azwaf",
    "azwaf",
    "cf_chl",
    "challenge-platform",
)
WAF_BODY_MARKERS = (
    "しばらくお待ちください",
    "ボットではない",
    "verifying that you are not a bot",
    "azure waf",
    "checking your browser",
    "just a moment",
    "enable javascript and cookies",
)


def persistent_context_kwargs(
    *,
    user_data_dir: str,
    headless: bool,
    maximized: bool = False,
) -> dict[str, Any]:
    """Launch args that look like real Chrome (no --no-sandbox warning)."""
    args = [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--disable-save-password-bubble",
        "--disable-password-generation",
        "--disable-features=PasswordManagerOnboarding,PasswordImport,PasswordCheck,AutofillServerCommunication",
    ]
    if maximized and not headless:
        args.append("--start-maximized")
    kwargs: dict[str, Any] = {
        "user_data_dir": user_data_dir,
        "channel": "chrome",
        "headless": headless,
        # Playwright defaults this to False, which injects --no-sandbox and
        # triggers Azure WAF on Julian Fashion.
        "chromium_sandbox": True,
        "args": args,
        "ignore_default_args": ["--enable-automation", "--no-sandbox"],
        "locale": "en-US",
        "timezone_id": "Asia/Tokyo",
    }
    if maximized and not headless:
        kwargs["viewport"] = None
    else:
        kwargs["viewport"] = {"width": 1440, "height": 1100}
    return kwargs


def launch_persistent_chrome(playwright, **kwargs) -> BrowserContext:
    """Launch real Chrome; fall back if sandbox cannot start on this PC."""
    try:
        return playwright.chromium.launch_persistent_context(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Chrome sandbox launch failed (%s); retrying without sandbox", exc)
        fallback = dict(kwargs)
        fallback["chromium_sandbox"] = False
        ignore = [a for a in list(fallback.get("ignore_default_args") or []) if a != "--no-sandbox"]
        fallback["ignore_default_args"] = ignore or ["--enable-automation"]
        return playwright.chromium.launch_persistent_context(**fallback)


def page_has_waf_challenge(page: Page) -> bool:
    try:
        url = (page.url or "").lower()
    except Exception:  # noqa: BLE001
        return False
    if any(marker in url for marker in WAF_URL_MARKERS):
        return True
    try:
        title = (page.title() or "").lower()
    except Exception:  # noqa: BLE001
        title = ""
    if "azure waf" in title or "just a moment" in title:
        return True
    try:
        body = (page.inner_text("body") or "")[:2500].lower()
    except Exception:  # noqa: BLE001
        return False
    return any(marker.lower() in body for marker in WAF_BODY_MARKERS)


def wait_out_waf(page: Page, timeout_ms: int = 45000) -> bool:
    """Wait until Azure/Cloudflare challenge leaves the page."""
    deadline = time.time() + max(1, timeout_ms) / 1000
    challenged = page_has_waf_challenge(page)
    if challenged:
        logger.info("Waiting for WAF/bot check to finish (%s)", page.url)
    while time.time() < deadline:
        if not page_has_waf_challenge(page):
            if challenged:
                logger.info("WAF/bot check cleared")
            return True
        try:
            page.wait_for_timeout(1200)
        except Exception:  # noqa: BLE001
            time.sleep(1.2)
    still = page_has_waf_challenge(page)
    if still:
        logger.warning("WAF/bot check still present after wait: %s", page.url)
    return not still


def wait_until_logged_in(check, *, page: Page, context: BrowserContext, timeout_s: int = 600, min_wait_s: int = 0) -> bool:
    """Poll until ``check()`` is true, or the user closes Chrome."""
    start = time.time()
    deadline = start + max(10, timeout_s)
    while time.time() < deadline:
        try:
            if not context.pages:
                return False
            if page.is_closed():
                return False
        except Exception:  # noqa: BLE001
            return False
        if time.time() - start >= max(0, min_wait_s):
            try:
                if check():
                    return True
            except Exception:  # noqa: BLE001
                pass
        try:
            page.wait_for_timeout(2000)
        except Exception:  # noqa: BLE001
            return False
    return False
