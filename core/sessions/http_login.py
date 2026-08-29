"""Member login over HTTP (curl_cffi Chrome TLS) — no visible browser.

Julian Fashion / Monti Boutique sit behind Azure WAF JS Challenge; HTTP GET
returns 403 and is aborted immediately (the challenge is not solved here).
Those sites fall back to background Chrome in cookie_login_service.
"""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse

from curl_cffi import requests

from core.sessions.login_recipes import RECIPES, recipe_for
from core.utils.playwright_cookies import cookies_are_waf_only

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

IMPERSONATE_CANDIDATES = (
    "chrome142",
    "chrome136",
    "chrome131",
    "chrome124",
    "chrome",
)

WAF_BODY_MARKERS = (
    "azure waf",
    "js challenge",
    "afd_azwaf",
    "challenge-platform",
    "just a moment",
    "checking your browser",
    "しばらくお待ちください",
    "ボットではない",
)

NAV_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

# Cookiebot consent cookie so the banner is not required on HTTP requests.
COOKIEBOT_CONSENT = (
    "{stamp:'%s',necessary:true,preferences:true,statistics:true,"
    "marketing:true,method:'explicit',ver:1,utc:%s,region:'jp'}"
)


class WafBlockedError(RuntimeError):
    """Site returned a bot-check page that HTTP cannot complete."""


class LoginFailedError(RuntimeError):
    """Reached the site but the member session was not established."""


def hidden_input_value(html: str, name: str) -> str:
    """Extract a hidden/input value by name (ViewState-safe)."""
    if not html or not name:
        return ""
    escaped = re.escape(name)
    for pat in (
        rf'<input[^>]*name=["\']{escaped}["\'][^>]*value=["\']([^"\']*)["\']',
        rf'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']{escaped}["\']',
    ):
        match = re.search(pat, html, re.I | re.S)
        if match:
            return unescape(match.group(1))
    return ""


def hidden_inputs(html: str) -> dict[str, str]:
    """Return name→value for all hidden inputs in HTML."""
    out: dict[str, str] = {}
    for tag in re.finditer(r"<input\b[^>]*>", html or "", re.I):
        chunk = tag.group(0)
        if not re.search(r'type=["\']hidden["\']', chunk, re.I):
            continue
        name_m = re.search(r'name=["\']([^"\']+)["\']', chunk, re.I)
        if not name_m:
            continue
        val_m = re.search(r'value=["\']([^"\']*)["\']', chunk, re.I)
        out[unescape(name_m.group(1))] = unescape(val_m.group(1)) if val_m else ""
    return out


def is_waf_response(status_code: int, url: str, body: str) -> bool:
    if any(marker in (url or "").lower() for marker in ("afd_azwaf", "azwaf", "cf_chl")):
        return True
    text = (body or "")[:4000].lower()
    if status_code in {403, 503} and any(marker in text for marker in WAF_BODY_MARKERS):
        return True
    return any(marker in text for marker in ("azure waf", "js challenge"))


def make_session() -> requests.Session:
    last_error = "unknown"
    for impersonate in IMPERSONATE_CANDIDATES:
        try:
            session = requests.Session(impersonate=impersonate)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{impersonate}: {exc}"
            continue
        session.headers.update(NAV_HEADERS)
        return session
    raise RuntimeError(f"curl_cffi impersonate failed ({last_error})")


def playwright_cookies_from_session(session: requests.Session, fallback_url: str) -> list[dict[str, Any]]:
    """Convert curl_cffi cookies into Playwright storage_state cookies."""
    host = urlparse(fallback_url).hostname or ""
    fallback_domain = _default_domain(host)
    jar = getattr(session.cookies, "jar", None) or session.cookies
    cookies: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for cookie in jar:
        name = getattr(cookie, "name", None)
        value = getattr(cookie, "value", None)
        if not name or value is None:
            continue
        domain = (getattr(cookie, "domain", None) or "").strip() or fallback_domain
        path = getattr(cookie, "path", None) or "/"
        key = (str(name), str(domain))
        if key in seen:
            continue
        seen.add(key)
        item: dict[str, Any] = {
            "name": str(name),
            "value": str(value),
            "domain": domain,
            "path": str(path),
            "httpOnly": _cookie_http_only(cookie),
            "secure": bool(getattr(cookie, "secure", False)),
            "sameSite": "Lax",
        }
        expires = getattr(cookie, "expires", None)
        if expires not in (None, "", 0, -1):
            try:
                item["expires"] = float(expires)
            except (TypeError, ValueError):
                pass
        cookies.append(item)
    return cookies


def _default_domain(host: str) -> str:
    host = (host or "").split(":")[0].strip().lower()
    if host.startswith("www."):
        return "." + host[4:]
    return "." + host if host else host


def _cookie_http_only(cookie: Any) -> bool:
    rest = getattr(cookie, "rest", None) or getattr(cookie, "_rest", None) or {}
    try:
        return any(str(key).lower() == "httponly" for key in rest)
    except Exception:  # noqa: BLE001
        return False


def _seed_cookiebot(session: requests.Session, host: str) -> None:
    import time
    import uuid

    domain = _default_domain(host)
    stamp = str(uuid.uuid4())
    utc = int(time.time() * 1000)
    try:
        session.cookies.set("CookieConsent", COOKIEBOT_CONSENT % (stamp, utc), domain=domain, path="/")
    except Exception:  # noqa: BLE001
        pass


def _get(session: requests.Session, url: str) -> Any:
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            resp = session.get(url, timeout=30, allow_redirects=True, headers=NAV_HEADERS)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("HTTP GET retry %s %s: %s", attempt + 1, url, exc)
            continue
        body = resp.text or ""
        if is_waf_response(resp.status_code, str(resp.url), body):
            raise WafBlockedError(f"WAF/bot check at {resp.url}")
        return resp
    raise last_exc or RuntimeError(f"HTTP GET failed {url}")


def _post(session: requests.Session, url: str, data: dict[str, str], referer: str) -> Any:
    headers = {
        **NAV_HEADERS,
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": f"{urlparse(url).scheme}://{urlparse(url).netloc}",
        "Referer": referer,
    }
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            resp = session.post(url, data=data, timeout=35, allow_redirects=True, headers=headers)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("HTTP POST retry %s %s: %s", attempt + 1, url, exc)
            continue
        body = resp.text or ""
        if is_waf_response(resp.status_code, str(resp.url), body):
            raise WafBlockedError(f"WAF/bot check at {resp.url}")
        return resp
    raise last_exc or RuntimeError(f"HTTP POST failed {url}")


def member_html_confirmed(site_code: str, url: str, html: str) -> bool:
    """True when HTTP HTML looks like a logged-in member page."""
    url_l = (url or "").lower()
    text = html or ""
    low = text.lower()
    if is_waf_response(200, url, text):
        return False
    if site_code == "buyma":
        if "/login" in url_l:
            return False
        return ("マイページ" in text or "出品する" in text) and "txtLoginPass" not in text
    if any(part in url_l for part in ("/login", "/signin", "user/login", "myaccount/login")):
        return False
    if "login_password" in low and "login_email" in low:
        return False
    if site_code == "eleonorabonucci":
        if "myaccount/login" in url_l or 'id="maincontent_login_loginp"' in low:
            return False
        return "/user/logout" in low or "sign out" in low or "logout" in low or (
            "myaccount" in url_l and "login" not in url_l
        )
    if "/user/logout" in low or "?mylogout=" in low or "/mylogout" in low:
        return True
    if re.search(r">\s*(log out|logout|sign out|esci)\s*<", low):
        return True
    if site_code == "minettiangeloonline":
        return "sign out" in low or "mylogout" in low
    return False


def http_login_ec(site_code: str, email: str, password: str) -> list[dict[str, Any]]:
    """POST the site's login form and return Playwright-shaped cookies."""
    email = (email or "").strip()
    password = (password or "").strip()
    if not email or not password:
        raise LoginFailedError("email/password empty")
    recipe = recipe_for(site_code)
    if recipe is None:
        raise LoginFailedError(f"unknown site {site_code}")
    session = make_session()
    host = urlparse(recipe.home_url).hostname or ""
    _seed_cookiebot(session, host)
    if site_code == "minettiangeloonline":
        return _login_minetti(session, email, password)
    if site_code == "eleonorabonucci":
        return _login_eleonora(session, email, password)
    if site_code in {"julian-fashion", "montiboutique"}:
        return _login_julian_family(session, recipe.home_url, recipe.login_url, email, password)
    raise LoginFailedError(f"no HTTP login for {site_code}")


def http_login_buyma(email: str, password: str) -> list[dict[str, Any]]:
    email = (email or "").strip()
    password = (password or "").strip()
    if not email or not password:
        raise LoginFailedError("email/password empty")
    session = make_session()
    login_url = RECIPES["buyma"].login_url
    page = _get(session, login_url)
    ticket = hidden_input_value(page.text, "onetimeticket")
    recaptcha = hidden_input_value(page.text, "recaptchaToken")
    action = "https://www.buyma.com/login/auth/"
    posted = _post(
        session,
        action,
        {
            "txtLoginId": email,
            "txtLoginPass": password,
            "recaptchaToken": recaptcha,
            "onetimeticket": ticket,
        },
        referer=str(page.url),
    )
    check = _get(session, "https://www.buyma.com/my/")
    html = check.text or posted.text
    url = str(check.url)
    if not member_html_confirmed("buyma", url, html):
        raise LoginFailedError("Buyma member page not confirmed")
    cookies = playwright_cookies_from_session(session, url)
    if not cookies or cookies_are_waf_only(cookies):
        raise LoginFailedError("Buyma cookies empty")
    logger.info("Buyma HTTP login ok (%s cookies)", len(cookies))
    return cookies


def _login_minetti(session: requests.Session, email: str, password: str) -> list[dict[str, Any]]:
    login_url = RECIPES["minettiangeloonline"].login_url
    page = _get(session, login_url)
    hidden = hidden_inputs(page.text)
    data = {
        "back": hidden.get("back", ""),
        "email": email,
        "password": password,
        "submitLogin": hidden.get("submitLogin", "1"),
    }
    posted = _post(session, str(page.url), data, referer=str(page.url))
    check = _get(session, "https://www.angelominetti.it/my-account")
    html = check.text or posted.text
    url = str(check.url)
    if not member_html_confirmed("minettiangeloonline", url, html):
        raise LoginFailedError("Minetti member page not confirmed")
    cookies = playwright_cookies_from_session(session, url)
    if not cookies:
        raise LoginFailedError("Minetti cookies empty")
    logger.info("Minetti HTTP login ok (%s cookies)", len(cookies))
    return cookies


def _login_eleonora(session: requests.Session, email: str, password: str) -> list[dict[str, Any]]:
    login_url = RECIPES["eleonorabonucci"].login_url
    page = _get(session, login_url)
    html = page.text
    data = hidden_inputs(html)
    data["__EVENTTARGET"] = "ctl00$MainContent$login$CmdLogin"
    data["__EVENTARGUMENT"] = data.get("__EVENTARGUMENT", "")
    data["ctl00$MainContent$login$LoginE"] = email
    data["ctl00$MainContent$login$LoginP"] = password
    data["ctl00$TextSearch"] = data.get("ctl00$TextSearch", "")
    data["ctl00$MainContent$login$AddEmail"] = data.get("ctl00$MainContent$login$AddEmail", "")
    action = urljoin(str(page.url), "./login")
    posted = _post(session, action, data, referer=str(page.url))
    check = _get(session, "https://eleonorabonucci.com/en/myaccount")
    body = check.text or posted.text
    url = str(check.url)
    if not member_html_confirmed("eleonorabonucci", url, body):
        raise LoginFailedError("Eleonora member page not confirmed")
    cookies = playwright_cookies_from_session(session, url)
    if not cookies:
        raise LoginFailedError("Eleonora cookies empty")
    logger.info("Eleonora HTTP login ok (%s cookies)", len(cookies))
    return cookies


def _login_julian_family(
    session: requests.Session,
    home_url: str,
    login_url: str,
    email: str,
    password: str,
) -> list[dict[str, Any]]:
    # First GET usually 403 Azure WAF JS Challenge — abort, do not retry impersonates.
    page = _get(session, home_url)
    hidden = hidden_inputs(page.text)
    form_key = hidden.get("form_key") or ""
    if not form_key:
        try:
            form_key = session.cookies.get("form_key") or ""
        except Exception:  # noqa: BLE001
            form_key = ""
    data = {
        "login_email": email,
        "login_password": password,
    }
    if form_key:
        data["form_key"] = str(form_key)
    posted = _post(session, login_url, data, referer=str(page.url))
    check = _get(session, home_url)
    html = check.text or posted.text
    url = str(check.url)
    site = "julian-fashion" if "julian-fashion" in home_url else "montiboutique"
    if not member_html_confirmed(site, url, html):
        raise LoginFailedError(f"{site} member page not confirmed")
    cookies = playwright_cookies_from_session(session, url)
    if not cookies or cookies_are_waf_only(cookies):
        raise LoginFailedError(f"{site} cookies empty")
    return cookies
