"""Per-site member login recipes (UI steps from 2026-08-15 screenshots).

Cookie save must NOT treat a guest header such as \"My Account\" as logged-in.
Start from the homepage and open the header account control — do not jump
straight to /user/login.

Julian Fashion
  Home: https://www.julian-fashion.com/en-JP
  Cookiebot → Allow all
  Click header person icon → dropdown \"SIGN IN WITH YOUR ACCOUNT\"
  Email / Password in that panel → LOG IN
  Member: Logout (/user/logout). Guest \"My Account\" / person icon is not enough.

Monti Boutique
  Home: https://www.montiboutique.com/en-JP
  Cookiebot → Allow all
  Click header person icon → click \"Log in\" in the dropdown
  Then email / password → Log in
  Footer \"My orders\" is guest text and is not a member session.

Minetti Angelo
  Home: https://www.angelominetti.it/
  Cookie → ACCEPT
  Click header \"SIGN IN\" (top right)
  Then login form email / password → form SIGN IN / #submit-login
  Guest header SIGN IN still points at /my-account.

Eleonora Bonucci
  Home: https://eleonorabonucci.com/
  Cookie Notice → \"Accept all\"
  Click header \"MY ACCOUNT\"
  Then #MainContent_login_LoginE / LoginP → CmdLogin SIGN IN
  Guest header always says My Account — that is NOT logged-in.

Buyma
  URL: https://www.buyma.com/login/
  Email: #txtLoginId   Password: #txtLoginPass → #login_do
  Member: /my/ with マイページ / 出品する
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from playwright.sync_api import Page

from core.utils.chrome_launch import page_has_waf_challenge, wait_out_waf

logger = logging.getLogger(__name__)

LOGOUT_LABELS = ("Log out", "Logout", "Sign out", "Sign Out", "Esci", "SIGN OUT")


@dataclass(frozen=True)
class LoginRecipe:
    site_code: str
    home_url: str
    login_url: str
    email_selectors: tuple[str, ...]
    password_selectors: tuple[str, ...]
    submit_selectors: tuple[str, ...]
    open_clicks: tuple[tuple[str, ...], ...] = ()
    guest_text: tuple[str, ...] = ()


RECIPES: dict[str, LoginRecipe] = {
    "julian-fashion": LoginRecipe(
        site_code="julian-fashion",
        home_url="https://www.julian-fashion.com/en-JP",
        login_url="https://www.julian-fashion.com/en-JP/user/login",
        email_selectors=(
            ".sub-menu--user input#login_email",
            ".sub-menu--user input[name='login_email']",
            "input#login_email",
            "input[placeholder='Email']",
        ),
        password_selectors=(
            ".sub-menu--user input#login_password",
            ".sub-menu--user input[name='login_password']",
            "input#login_password",
            "input[placeholder='Password']",
        ),
        submit_selectors=(
            ".sub-menu--user button.js-modal-login",
            ".sub-menu--user button:has-text('LOG IN')",
            "button.js-modal-login",
            "button:has-text('LOG IN')",
            "#user-login-submit",
        ),
        open_clicks=(
            (
                "a[aria-label='Log in to your account']",
                "li.item.user-menu a.js-toggle",
                ".properties--usermenu a.js-toggle",
                ".user-menu a.js-toggle",
            ),
        ),
        guest_text=("SIGN IN WITH YOUR ACCOUNT",),
    ),
    "montiboutique": LoginRecipe(
        site_code="montiboutique",
        home_url="https://www.montiboutique.com/en-JP",
        login_url="https://www.montiboutique.com/en-JP/user/login",
        email_selectors=(
            "input#login_email",
            "input[name='login_email']",
        ),
        password_selectors=(
            "input#login_password",
            "input[name='login_password']",
        ),
        submit_selectors=(
            ".sub-menu--user button.js-modal-login",
            "button.js-modal-login",
            "button:has-text('Log in')",
            "#user-login-submit",
        ),
        open_clicks=(
            (
                "a[aria-label='account']",
                "a[aria-label='Log in to your account']",
                "li.item.user-menu a.js-toggle",
                ".user-menu a.js-toggle",
            ),
            (
                "a[href*='/user/login']",
                ".sub-menu--user a:has-text('Log in')",
                "a:has-text('Log in')",
            ),
        ),
        guest_text=("SIGN IN WITH YOUR ACCOUNT",),
    ),
    "minettiangeloonline": LoginRecipe(
        site_code="minettiangeloonline",
        home_url="https://www.angelominetti.it/",
        login_url="https://www.angelominetti.it/login",
        email_selectors=("form#login-form input[name='email']", "input[type='email'][name='email']", "input[name='email']"),
        password_selectors=("form#login-form input[name='password']", "input[type='password'][name='password']"),
        submit_selectors=(
            "button#submit-login",
            "button[data-link-action='sign-in']",
            "form#login-form button[type='submit']",
        ),
        open_clicks=(
            (
                "header a:has-text('SIGN IN')",
                "a[href*='my-account']:has-text('SIGN IN')",
                "a[href*='/login']:has-text('SIGN IN')",
            ),
        ),
        guest_text=("LOG IN TO YOUR ACCOUNT",),
    ),
    "eleonorabonucci": LoginRecipe(
        site_code="eleonorabonucci",
        home_url="https://eleonorabonucci.com/",
        login_url="https://eleonorabonucci.com/en/myaccount/login",
        email_selectors=("#MainContent_login_LoginE", "input[name='ctl00$MainContent$login$LoginE']"),
        password_selectors=("#MainContent_login_LoginP", "input[name='ctl00$MainContent$login$LoginP']"),
        submit_selectors=("#MainContent_login_CmdLogin",),
        open_clicks=(
            (
                "a[href*='/en/myaccount/login']",
                "a:has-text('MY ACCOUNT')",
                "a:has-text('My Account')",
            ),
        ),
        guest_text=("If you already have an account, login", "SIGN IN FORGOT PASSWORD"),
    ),
    "buyma": LoginRecipe(
        site_code="buyma",
        home_url="https://www.buyma.com/login/",
        login_url="https://www.buyma.com/login/",
        email_selectors=("#txtLoginId", "input[name='txtLoginId']"),
        password_selectors=("#txtLoginPass", "input[name='txtLoginPass']"),
        submit_selectors=("#login_do", "input[type='submit']"),
        guest_text=("すでに会員の方",),
    ),
}


def recipe_for(site_code: str) -> LoginRecipe | None:
    return RECIPES.get(site_code)


def login_fields_ready(page: Page, site_code: str = "") -> bool:
    """True only when a login password field is visible (not a newsletter email box)."""
    if login_form_visible(page):
        return True
    recipe = recipe_for(site_code)
    if not recipe:
        return False
    for sel in recipe.password_selectors:
        try:
            if page.locator(f"{sel} >> visible=true").count() > 0:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def login_form_visible(page: Page) -> bool:
    try:
        return page.locator("input[type='password']:visible").count() > 0
    except Exception:  # noqa: BLE001
        return False


def _has_logout_control(page: Page) -> bool:
    """Logout may sit in a closed account menu — count it even if not visible."""
    for label in LOGOUT_LABELS:
        try:
            if page.get_by_role("link", name=re.compile(rf"^{re.escape(label)}$", re.I)).count() > 0:
                return True
            if page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I)).count() > 0:
                return True
            loc = page.locator("a, button").filter(has_text=re.compile(rf"^{re.escape(label)}$", re.I))
            if loc.count() > 0:
                return True
        except Exception:  # noqa: BLE001
            continue
    try:
        hrefs = page.locator(
            "a[href*='logout' i], a[href*='log-out' i], a[href*='signout' i], "
            "a[href*='sign-out' i], a[href*='mylogout' i]"
        )
        if hrefs.count() > 0:
            return True
    except Exception:  # noqa: BLE001
        try:
            hrefs = page.locator("a[href*='logout'], a[href*='log-out'], a[href*='signout'], a[href*='mylogout']")
            if hrefs.count() > 0:
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def member_session_confirmed(page: Page, site_code: str = "") -> bool:
    """True only for a real member session — never for a guest 'My Account' header.

    Julian/Monti keep aria-label \"Log in to your account\" on the account icon
    even after login; a Logout / /user/logout link is the real member signal.
    """
    if login_form_visible(page):
        return False
    try:
        if page_has_waf_challenge(page):
            return False
    except Exception:  # noqa: BLE001
        pass
    url = ""
    try:
        url = (page.url or "").lower()
    except Exception:  # noqa: BLE001
        return False
    if any(x in url for x in ("/login", "/signin", "/register", "user/login", "myaccount/login")):
        return False

    if site_code == "buyma":
        body = ""
        try:
            body = (page.inner_text("body") or "")[:4000]
        except Exception:  # noqa: BLE001
            body = ""
        return ("マイページ" in body or "出品する" in body) and not (page.url or "").lower().startswith("login")

    if _has_logout_control(page):
        return True

    body = ""
    try:
        body = (page.inner_text("body") or "")[:4000]
    except Exception:  # noqa: BLE001
        body = ""
    body_l = body.lower()
    if any(x in body_l for x in ("log out", "logout", "sign out", " esci", "esci\n")):
        return True

    recipe = recipe_for(site_code)
    if recipe:
        for marker in recipe.guest_text:
            if marker.lower() in body_l:
                return False
    if "sign in with your account" in body_l:
        return False
    if "if you already have an account, login" in body_l:
        return False

    if site_code == "eleonorabonucci":
        try:
            still_login = page.locator("a[href*='myaccount/login']").count() > 0
            member_acc = page.locator("a[href*='myaccount']").count() > 0
            if member_acc and not still_login:
                return True
        except Exception:  # noqa: BLE001
            pass
        return False
    return False


def _on_login_url(page: Page, login_url: str) -> bool:
    current = (page.url or "").split("?")[0].rstrip("/").lower()
    target = login_url.split("?")[0].rstrip("/").lower()
    return current == target or current.endswith(target.split("://", 1)[-1])


def dismiss_consent_overlays(page: Page) -> None:
    """Clear Cookiebot / Notice / OneTrust so header login controls are clickable."""
    try:
        page.wait_for_selector(
            "#CybotCookiebotDialog:visible, #CookiebotWidget:visible, button:has-text('Accept all')",
            timeout=4000,
        )
    except Exception:  # noqa: BLE001
        pass
    for label in ("Accept all", "Accept All", "ACCEPT ALL", "Allow all", "Allow All", "ACCEPT"):
        try:
            btn = page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I)).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=2500, force=True)
                page.wait_for_timeout(700)
                break
        except Exception:  # noqa: BLE001
            continue
        try:
            link = page.get_by_role("link", name=re.compile(rf"^{re.escape(label)}$", re.I)).first
            if link.count() > 0 and link.is_visible():
                link.click(timeout=2500, force=True)
                page.wait_for_timeout(700)
                break
        except Exception:  # noqa: BLE001
            continue
    for sel in (
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "button:has-text('Accept all')",
        "a:has-text('Accept all')",
    ):
        loc = page.locator(sel).first
        try:
            if loc.count() > 0:
                loc.click(timeout=2500, force=True)
                page.wait_for_timeout(600)
                break
        except Exception:  # noqa: BLE001
            continue
    try:
        page.evaluate(
            """() => {
              for (const id of ['CybotCookiebotDialog', 'CookiebotWidget', 'CybotCookiebotDialogBodyUnderlay']) {
                const el = document.getElementById(id);
                if (el) el.style.display = 'none';
              }
              document.body.style.overflow = '';
            }"""
        )
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(400)


def _click_first(page: Page, selectors: tuple[str, ...]) -> bool:
    for sel in selectors:
        loc = page.locator(sel)
        try:
            n = min(loc.count(), 8)
        except Exception:  # noqa: BLE001
            n = 0
        # Prefer the last visible match (header controls are often later in the DOM).
        for i in range(n - 1, -1, -1):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                el.scroll_into_view_if_needed(timeout=3000)
                el.click(timeout=4000, force=True)
                page.wait_for_timeout(800)
                return True
            except Exception:  # noqa: BLE001
                continue
    return False


def open_login_ui(page: Page, site_code: str, *, fast: bool = False) -> bool:
    """Homepage → cookie banner → header account control → visible login fields."""
    if login_fields_ready(page, site_code):
        return True
    recipe = recipe_for(site_code)
    if recipe is None:
        return False
    page.wait_for_timeout(400 if fast else 800)
    dismiss_consent_overlays(page)
    for i, step in enumerate(recipe.open_clicks):
        _click_first(page, step)
        last = i == len(recipe.open_clicks) - 1
        wait_s = 3 if fast else (12 if last else 2)
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if login_fields_ready(page, site_code):
                return True
            try:
                if page_has_waf_challenge(page):
                    wait_out_waf(page, timeout_ms=8000)
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(400 if fast else 800)
    return login_fields_ready(page, site_code)


def _fill_all_visible(page: Page, selectors: tuple[str, ...], value: str, timeout_ms: int) -> int:
    """Type into every visible match. Julian/Monti duplicate hidden header fields."""
    filled = 0
    last_exc: Exception | None = None
    for sel in selectors:
        loc = page.locator(f"{sel} >> visible=true")
        try:
            loc.first.wait_for(state="visible", timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
        try:
            n = min(loc.count(), 6)
        except Exception:  # noqa: BLE001
            n = 1
        for i in range(n):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                el.click(timeout=2500, force=True)
                el.fill(value, force=True, timeout=4000)
                filled += 1
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                try:
                    el.evaluate(
                        """(node, v) => {
                          node.focus();
                          node.value = v;
                          node.dispatchEvent(new Event('input', {bubbles: true}));
                          node.dispatchEvent(new Event('change', {bubbles: true}));
                        }""",
                        value,
                    )
                    filled += 1
                except Exception:  # noqa: BLE001
                    continue
        if filled:
            return filled
    if filled == 0 and last_exc:
        raise last_exc
    return filled


def _visible_email_value(page: Page, selectors: tuple[str, ...]) -> str:
    for sel in selectors:
        loc = page.locator(f"{sel} >> visible=true").first
        try:
            if loc.count() > 0 and loc.is_visible():
                return (loc.input_value() or "").strip()
        except Exception:  # noqa: BLE001
            continue
    return ""


def fill_and_submit_login(
    page: Page,
    site_code: str,
    email: str,
    password: str,
    *,
    fast: bool = False,
) -> bool:
    """Follow the homepage header login UI, type into VISIBLE fields, click submit."""
    recipe = recipe_for(site_code)
    if recipe is None:
        logger.warning("No login recipe for %s", site_code)
        return False
    if not email or not password:
        logger.warning("%s: email/password empty; cannot auto-fill", site_code)
        return False
    try:
        if not login_fields_ready(page, site_code):
            current = (page.url or "").lower()
            home_host = recipe.home_url.split("://", 1)[-1].split("/", 1)[0].lower()
            if home_host not in current:
                page.goto(recipe.home_url, wait_until="domcontentloaded", timeout=60000)
            wait_out_waf(page, timeout_ms=20000 if fast else 45000)
            dismiss_consent_overlays(page)
            opened = open_login_ui(page, site_code, fast=fast)
            if not opened:
                page.goto(recipe.login_url, wait_until="domcontentloaded", timeout=60000)
                wait_out_waf(page, timeout_ms=15000 if fast else 20000)
                dismiss_consent_overlays(page)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s open login UI failed: %s", site_code, exc)
        return False
    page.wait_for_timeout(400 if fast else 800)
    dismiss_consent_overlays(page)
    if not login_fields_ready(page, site_code):
        open_login_ui(page, site_code, fast=fast)
    try:
        n_email = _fill_all_visible(page, recipe.email_selectors, email, timeout_ms=18000)
        n_pass = _fill_all_visible(page, recipe.password_selectors, password, timeout_ms=8000)
        shown = _visible_email_value(page, recipe.email_selectors)
        if n_email < 1 or n_pass < 1 or shown.lower() != email.lower():
            logger.warning(
                "%s visible login fields not filled (email=%s pass=%s shown=%s)",
                site_code,
                n_email,
                n_pass,
                "yes" if shown else "empty",
            )
            return False
        page.wait_for_timeout(400)
        clicked = False
        for sel in recipe.submit_selectors:
            loc = page.locator(sel).first
            try:
                if loc.count() > 0:
                    loc.click(timeout=4000, force=True)
                    clicked = True
                    break
            except Exception:  # noqa: BLE001
                continue
        if not clicked:
            try:
                btn = page.get_by_role("button", name=re.compile(r"^(log in|login|sign in|sign-in)$", re.I)).first
                if btn.count() > 0:
                    btn.click(timeout=4000, force=True)
                    clicked = True
            except Exception:  # noqa: BLE001
                pass
        if not clicked:
            try:
                page.locator(f"{recipe.password_selectors[0]} >> visible=true").first.press("Enter", timeout=5000)
            except Exception:  # noqa: BLE001
                logger.warning("%s submit click missed after fill", site_code)
        page.wait_for_timeout(3500 if site_code == "eleonorabonucci" else 2500)
        logger.info("%s login form filled and submitted", site_code)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s auto-fill failed: %s", site_code, exc)
        return False
