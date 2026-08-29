"""Save member cookies: HTTP first (no window), background Chrome only if WAF blocks."""

from __future__ import annotations

from pathlib import Path

from core.buyma.buyma_cookie_service import save_cookie_list
from core.config import clear_settings_cache, get_settings
from core.scrapers.sites import SCRAPER_REGISTRY
from core.sessions.ec_session_service import save_http_cookies, save_storage_state, storage_state_path
from core.sessions.http_login import (
    LoginFailedError,
    WafBlockedError,
    http_login_buyma,
    http_login_ec,
)
from core.sessions.login_recipes import (
    dismiss_consent_overlays,
    fill_and_submit_login,
    login_fields_ready,
    login_form_visible,
    member_session_confirmed,
    open_login_ui,
    recipe_for,
)
from core.utils.chrome_launch import (
    launch_persistent_chrome,
    page_has_waf_challenge,
    persistent_context_kwargs,
    wait_out_waf,
    wait_until_logged_in,
)
from core.utils.chrome_profile import prepare_chrome_profile
from core.utils.cookie_helper_log import log_cookie_event
from core.utils.playwright_cookies import clear_waf_cookies
from core.utils.playwright_runtime import acquire_playwright, release_playwright

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NEED_LOGIN = 2

# Azure WAF JS Challenge cannot be completed over HTTP. Background Chrome
# executes the site's own script; a visible window is last resort only.
WAF_SITES = frozenset({"julian-fashion", "montiboutique"})


def save_ec_login(site_code: str) -> int:
    clear_settings_cache()
    scraper_cls = SCRAPER_REGISTRY.get(site_code)
    if not scraper_cls:
        log_cookie_event(f"Unknown site {site_code}")
        return EXIT_ERROR
    scraper = scraper_cls()
    account = scraper.account()
    email = (account.email or "").strip()
    password = (account.password or "").strip()
    if not email or not password:
        log_cookie_event(f"EC {site_code} email/password empty")
        return EXIT_NEED_LOGIN

    try:
        cookies = http_login_ec(site_code, email, password)
        path = save_http_cookies(site_code, cookies, login_verified=True)
        log_cookie_event(f"EC {site_code} HTTP login saved -> {path}")
        return EXIT_OK
    except WafBlockedError as exc:
        log_cookie_event(f"EC {site_code} HTTP blocked ({exc}); background Chrome")
    except LoginFailedError as exc:
        log_cookie_event(f"EC {site_code} HTTP login failed: {exc}")
        if site_code not in WAF_SITES:
            return EXIT_NEED_LOGIN
    except Exception as exc:  # noqa: BLE001
        log_cookie_event(f"EC {site_code} HTTP error: {exc}")

    code = _playwright_ec_save(site_code, email, password, headless=True)
    if code == EXIT_OK:
        return EXIT_OK
    log_cookie_event(f"EC {site_code} headless Chrome failed; trying visible Chrome")
    return _playwright_ec_save(site_code, email, password, headless=False)


def save_buyma_login() -> int:
    clear_settings_cache()
    settings = get_settings()
    email = (settings.buyma_account_email or "").strip()
    password = (settings.buyma_account_password or "").strip()
    if not email or not password:
        log_cookie_event("Buyma email/password empty")
        return EXIT_NEED_LOGIN
    try:
        cookies = http_login_buyma(email, password)
        saved = save_cookie_list(cookies, settings.buyma_cookies_path, source="http")
        log_cookie_event(f"Buyma HTTP login saved {saved.get('cookie_count')} cookies")
        return EXIT_OK
    except WafBlockedError as exc:
        log_cookie_event(f"Buyma HTTP blocked ({exc}); background Chrome")
    except LoginFailedError as exc:
        log_cookie_event(f"Buyma HTTP login failed: {exc}; background Chrome (captcha)")
    except Exception as exc:  # noqa: BLE001
        log_cookie_event(f"Buyma HTTP error: {exc}")

    code = _playwright_buyma_save(email, password, headless=True)
    if code == EXIT_OK:
        return EXIT_OK
    log_cookie_event("Buyma headless Chrome failed; trying visible Chrome")
    return _playwright_buyma_save(email, password, headless=False)


def _confirmed_ec(scraper, page) -> bool:
    scraper.page = page
    try:
        scraper._dismiss_popups(page)
    except Exception:  # noqa: BLE001
        pass
    try:
        return member_session_confirmed(page, scraper.site_code)
    except Exception as exc:  # noqa: BLE001
        log_cookie_event(f"login check failed: {exc}")
        return False


def _playwright_ec_save(site_code: str, email: str, password: str, *, headless: bool) -> int:
    settings = get_settings()
    scraper_cls = SCRAPER_REGISTRY[site_code]
    scraper = scraper_cls()
    profile = Path(settings.chatgpt_profile_path).parent / f"ec-{scraper.profile_subdir}"
    prepare_chrome_profile(profile)
    session_path = storage_state_path(site_code)
    log_cookie_event(f"EC {site_code} Chrome headless={headless} profile={profile} session={session_path}")
    pw = acquire_playwright()
    context = None
    try:
        context = launch_persistent_chrome(
            pw,
            **persistent_context_kwargs(
                user_data_dir=str(profile),
                headless=headless,
                maximized=not headless,
            ),
        )
        page = context.pages[0] if context.pages else context.new_page()
        scraper.page = page
        scraper.context = context
        cleared = clear_waf_cookies(context)
        if cleared:
            log_cookie_event(f"Cleared {cleared} stale WAF cookies")
        recipe = recipe_for(site_code)
        start_url = (recipe.home_url if recipe else None) or scraper.base_url or scraper.login_url
        page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
        wait_out_waf(page, timeout_ms=25000 if headless else 45000)
        try:
            scraper._dismiss_popups(page)
        except Exception:  # noqa: BLE001
            pass
        try:
            dismiss_consent_overlays(page)
            open_login_ui(page, site_code, fast=True)
        except Exception as exc:  # noqa: BLE001
            log_cookie_event(f"open login UI skipped: {exc}")

        filled_once = False
        ticks = {"n": 0}

        def _tick() -> bool:
            nonlocal filled_once
            ticks["n"] += 1
            try:
                scraper._dismiss_popups(page)
            except Exception:  # noqa: BLE001
                pass
            try:
                waf = page_has_waf_challenge(page)
            except Exception:  # noqa: BLE001
                waf = False
            if not login_fields_ready(page, site_code):
                if ticks["n"] % 2 == 1:
                    dismiss_consent_overlays(page)
                    opened = open_login_ui(page, site_code, fast=True)
                    log_cookie_event(f"EC {site_code} login UI={'open' if opened else 'waiting'}")
                if ticks["n"] == 8 and recipe_for(site_code):
                    fallback = recipe_for(site_code).login_url
                    log_cookie_event(f"EC {site_code} fallback {fallback}")
                    try:
                        page.goto(fallback, wait_until="domcontentloaded", timeout=60000)
                        wait_out_waf(page, timeout_ms=15000)
                        dismiss_consent_overlays(page)
                    except Exception as exc:  # noqa: BLE001
                        log_cookie_event(f"EC {site_code} fallback failed: {exc}")
                if waf:
                    return False
                return _confirmed_ec(scraper, page)
            if email and password:
                from core.sessions.login_recipes import _visible_email_value

                recipe_now = recipe_for(site_code)
                shown = _visible_email_value(page, recipe_now.email_selectors) if recipe_now else ""
                if shown.lower() != email.lower():
                    filled_once = fill_and_submit_login(
                        page, site_code, email, password, fast=True
                    )
                    log_cookie_event(f"EC {site_code} auto-fill={'ok' if filled_once else 'skipped'}")
                return False
            if waf:
                return False
            return _confirmed_ec(scraper, page)

        ok = wait_until_logged_in(
            _tick,
            page=page,
            context=context,
            timeout_s=90 if headless else 180,
            min_wait_s=2,
        )
        if not ok:
            log_cookie_event(f"EC {site_code} member session not confirmed (headless={headless})")
            return EXIT_NEED_LOGIN
        try:
            if page_has_waf_challenge(page) or login_form_visible(page):
                log_cookie_event(f"EC {site_code} refusing to save (still WAF or login form)")
                return EXIT_NEED_LOGIN
        except Exception:  # noqa: BLE001
            return EXIT_NEED_LOGIN
        path = save_storage_state(context, site_code, login_verified=True)
        log_cookie_event(f"EC {site_code} Chrome saved verified session -> {path}")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001
        log_cookie_event(f"EC {site_code} Chrome save failed: {exc}")
        return EXIT_ERROR
    finally:
        if context:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass
        release_playwright()


def _playwright_buyma_save(email: str, password: str, *, headless: bool) -> int:
    settings = get_settings()
    profile = Path(settings.buyma_profile_path)
    prepare_chrome_profile(profile)
    log_cookie_event(f"Buyma Chrome headless={headless} profile={profile}")
    pw = acquire_playwright()
    context = None
    try:
        context = launch_persistent_chrome(
            pw,
            **persistent_context_kwargs(
                user_data_dir=str(profile),
                headless=headless,
                maximized=not headless,
            ),
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.buyma.com/login/", wait_until="domcontentloaded", timeout=60000)
        wait_out_waf(page, timeout_ms=20000)

        def _tick() -> bool:
            try:
                if page_has_waf_challenge(page):
                    return False
            except Exception:  # noqa: BLE001
                return False
            if login_form_visible(page) and email and password:
                from core.sessions.login_recipes import _visible_email_value, recipe_for as _recipe

                recipe = _recipe("buyma")
                shown = _visible_email_value(page, recipe.email_selectors) if recipe else ""
                if shown.lower() != email.lower():
                    filled = fill_and_submit_login(page, "buyma", email, password, fast=True)
                    log_cookie_event(f"Buyma auto-fill={'ok' if filled else 'skipped'}")
                return False
            try:
                return member_session_confirmed(page, "buyma")
            except Exception as exc:  # noqa: BLE001
                log_cookie_event(f"Buyma login check failed: {exc}")
                return False

        ok = wait_until_logged_in(
            _tick,
            page=page,
            context=context,
            timeout_s=90 if headless else 180,
            min_wait_s=2,
        )
        if not ok:
            log_cookie_event(f"Buyma member session not confirmed (headless={headless})")
            return EXIT_NEED_LOGIN
        try:
            if page_has_waf_challenge(page) or login_form_visible(page):
                log_cookie_event("Buyma refusing to save (still WAF or login form)")
                return EXIT_NEED_LOGIN
        except Exception:  # noqa: BLE001
            return EXIT_NEED_LOGIN
        saved = save_cookie_list(list(context.cookies()), settings.buyma_cookies_path, source="playwright")
        log_cookie_event(f"Buyma Chrome saved {saved.get('cookie_count')} cookies")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001
        log_cookie_event(f"Buyma Chrome save failed: {exc}")
        return EXIT_ERROR
    finally:
        if context:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass
        release_playwright()
