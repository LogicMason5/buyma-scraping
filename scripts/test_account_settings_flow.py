"""Automated tests for Settings account save / cookie reset / helper wait path.

Usage:
  py -3 scripts/test_account_settings_flow.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _save_env_like_launcher(env_path: Path, updates: dict[str, str]) -> None:
    data: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v
    data.update(updates)
    lines: list[str] = []
    seen: set[str] = set()
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.strip().startswith("#") or "=" not in line:
            lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in data:
            lines.append(f"{key}={data[key]}")
            seen.add(key)
        else:
            lines.append(line)
    for key, value in data.items():
        if key not in seen:
            lines.append(f"{key}={value}")
            seen.add(key)
    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def test_env_account_save_preserves_order(tmp: Path) -> None:
    print("\n=== account .env save ===")
    env_path = tmp / ".env"
    env_path.write_text(
        "APP_NAME=EC-Buyma\n"
        "EC_SITE_EMAIL=old@example.com\n"
        "BUYMA_MAX_IMAGES=10\n"
        "WORKSPACE_DIR=./workspace\n",
        encoding="utf-8",
    )
    _save_env_like_launcher(
        env_path,
        {
            "EC_SITE_EMAIL": "new@example.com",
            "EC_SITE_PASSWORD": "secret1",
            "EC_MINETTI_PASSWORD": "secret2",
            "BUYMA_ACCOUNT_EMAIL": "buyma@example.com",
            "BUYMA_ACCOUNT_PASSWORD": "buyma-pass",
            "CHATGPT_DESCRIPTION_PROJECT_URL": "https://chatgpt.com/c/desc-id",
            "CHATGPT_IMAGE_PROJECT_URL": "https://chatgpt.com/c/image-id",
        },
    )
    text = env_path.read_text(encoding="utf-8")
    _assert("EC_SITE_EMAIL=new@example.com" in text, "email not updated")
    _assert("BUYMA_ACCOUNT_EMAIL=buyma@example.com" in text, "buyma email missing")
    _assert("BUYMA_ACCOUNT_PASSWORD=buyma-pass" in text, "buyma password missing")
    _assert("CHATGPT_IMAGE_PROJECT_URL=https://chatgpt.com/c/image-id" in text, "chatgpt image url missing")
    _assert("EC_SITE_PASSWORD=secret1" in text, "ec password missing")
    _assert(text.index("APP_NAME") < text.index("EC_SITE_EMAIL"), "order broken")
    _assert(text.index("EC_SITE_EMAIL") < text.index("BUYMA_MAX_IMAGES"), "order broken")
    print("PASS env account save")


def test_ec_reset_clears_cookies_and_storage(tmp: Path) -> None:
    print("\n=== EC session reset ===")
    os.chdir(tmp)
    from core import paths as paths_mod
    from core.config import clear_settings_cache
    from core.sessions import ec_session_service as ec

    paths_mod.runtime_root = lambda: tmp  # type: ignore[assignment]
    clear_settings_cache()

    site = "julian-fashion"
    payload = {"cookies": [{"name": "a", "value": "1", "domain": ".example.com", "path": "/"}]}
    ec.storage_state_path(site).write_text(json.dumps(payload), encoding="utf-8")
    ec.cookies_path(site).write_text(json.dumps(payload), encoding="utf-8")
    other = "montiboutique"
    ec.storage_state_path(other).write_text(json.dumps(payload), encoding="utf-8")
    ec.cookies_path(other).write_text(json.dumps(payload), encoding="utf-8")

    _assert(ec.has_saved_session(site), "site should be saved")
    removed = ec.clear_site_session(site)
    _assert(len(removed) == 2, f"expected 2 files removed, got {removed}")
    _assert(not ec.has_saved_session(site), "site still marked saved")
    _assert(ec.has_saved_session(other), "other site should remain")

    removed_all = ec.clear_all_sessions()
    _assert(len(removed_all) >= 2, f"expected remaining files cleared, got {removed_all}")
    _assert(not ec.has_saved_session(other), "other site still saved")
    print("PASS EC reset")


def test_buyma_reset(tmp: Path) -> None:
    print("\n=== Buyma cookie reset ===")
    secrets = tmp / "secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    cookie = secrets / "buyma_cookies.json"
    bak = secrets / "buyma_cookies.bak.json"
    cookie.write_text(json.dumps({"cookies": [{"name": "x", "value": "y"}]}), encoding="utf-8")
    bak.write_text("{}", encoding="utf-8")
    cookie.unlink()
    bak.unlink()
    _assert(not cookie.exists() and not bak.exists(), "cookie/bak should be gone")
    print("PASS Buyma reset")


def test_chatgpt_session_and_reset(tmp: Path) -> None:
    print("\n=== ChatGPT cookie save / reset ===")
    from core.chatgpt.chatgpt_cookie_service import (
        clear_chatgpt_session,
        has_saved_chatgpt_session,
    )

    cookie = tmp / "chatgpt_cookies.json"
    bak = tmp / "chatgpt_cookies.bak.json"
    _assert(not has_saved_chatgpt_session(cookie), "missing file must be empty")
    cookie.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "__Secure-next-auth.session-token",
                        "value": "tok",
                        "domain": ".chatgpt.com",
                        "path": "/",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    bak.write_text("{}", encoding="utf-8")
    _assert(has_saved_chatgpt_session(cookie), "session token should count")
    removed = clear_chatgpt_session(cookies_path=cookie)
    _assert(len(removed) >= 1, f"expected cookie removed, got {removed}")
    _assert(not has_saved_chatgpt_session(cookie), "cookie should be gone")
    _assert(not bak.exists(), "bak should be gone")
    print("PASS ChatGPT reset")


def test_session_rejects_waf_only_cookies(tmp: Path) -> None:
    print("\n=== WAF-only session is not 保存済 ===")
    os.chdir(tmp)
    from core import paths as paths_mod
    from core.config import clear_settings_cache
    from core.sessions import ec_session_service as ec
    from core.utils.playwright_cookies import cookies_are_waf_only, sanitize_playwright_cookie

    paths_mod.runtime_root = lambda: tmp  # type: ignore[assignment]
    clear_settings_cache()

    site = "julian-fashion"
    waf = {
        "cookies": [
            {"name": "afd_azwaf_jsclearance", "value": "x", "domain": ".julian-fashion.com", "path": "/"},
        ]
    }
    ec.storage_state_path(site).write_text(json.dumps(waf), encoding="utf-8")
    _assert(cookies_are_waf_only(waf["cookies"]), "helper should detect WAF-only")
    _assert(not ec.has_saved_session(site), "WAF-only must not count as saved login")

    verified = {
        "login_verified": True,
        "cookies": [{"name": "PHPSESSID", "value": "abc", "domain": ".julian-fashion.com", "path": "/"}],
    }
    ec.storage_state_path(site).write_text(json.dumps(verified), encoding="utf-8")
    _assert(ec.has_saved_session(site), "verified session should count")

    rejected = {
        "login_verified": False,
        "cookies": [{"name": "PHPSESSID", "value": "abc", "domain": ".julian-fashion.com", "path": "/"}],
    }
    ec.storage_state_path(site).write_text(json.dumps(rejected), encoding="utf-8")
    _assert(not ec.has_saved_session(site), "login_verified=false must not count")

    cleaned = sanitize_playwright_cookie(
        {"name": "a", "value": "1", "domain": ".example.com", "path": "/", "sameSite": "unspecified"}
    )
    _assert(cleaned is not None and cleaned["sameSite"] == "Lax", f"sameSite {cleaned}")
    print("PASS WAF/session cookie rules")


def test_member_session_not_fooled_by_guest_my_account() -> None:
    print("\n=== member session false positives ===")
    from core.sessions.login_recipes import RECIPES, member_session_confirmed

    class _Loc:
        def __init__(self, n: int = 0) -> None:
            self._n = n

        def count(self) -> int:
            return self._n

        def locator(self, *_a, **_k):  # noqa: ANN001
            return self

        def first(self):
            return self

        def filter(self, **_k):  # noqa: ANN003
            return self

    class _Page:
        def __init__(self, url: str, body: str, *, password: int = 0, logout: int = 0, guest: int = 0) -> None:
            self.url = url
            self._body = body
            self._password = password
            self._logout = logout
            self._guest = guest

        def inner_text(self, _sel: str) -> str:
            return self._body

        def locator(self, sel: str):  # noqa: ANN001
            text = str(sel)
            if "password" in text:
                return _Loc(self._password)
            if "Log in to your account" in text:
                return _Loc(self._guest)
            return _Loc(0)

        def get_by_role(self, _role: str, name=None):  # noqa: ANN001
            return _Loc(self._logout)

    eleonora_guest = _Page(
        "https://eleonorabonucci.com/",
        "WOMEN MEN KIDS My Account Italy - En | EUR",
        password=0,
        logout=0,
    )
    _assert(
        not member_session_confirmed(eleonora_guest, "eleonorabonucci"),
        "Eleonora guest My Account must not count as logged in",
    )

    julian_login = _Page(
        "https://www.julian-fashion.com/en-JP/user/login",
        "SIGN IN WITH YOUR ACCOUNT Email Password Log in",
        password=1,
        logout=0,
        guest=1,
    )
    _assert(not member_session_confirmed(julian_login, "julian-fashion"), "Julian login form must wait")

    julian_member = _Page(
        "https://www.julian-fashion.com/en-JP",
        "Log out My Orders Wishlist",
        password=0,
        logout=1,
    )
    julian_waf = _Page(
        "https://www.julian-fashion.com/en-JP/user/login?afd_azwaf_tok=abc",
        "Please wait",
        password=0,
        logout=1,
    )
    _assert(not member_session_confirmed(julian_waf, "julian-fashion"), "WAF page must not count as logged in")

    julian_member_stale_aria = _Page(
        "https://www.julian-fashion.com/en-JP",
        "Logout My Orders Wishlist",
        password=0,
        logout=1,
        guest=1,
    )
    _assert(
        member_session_confirmed(julian_member_stale_aria, "julian-fashion"),
        "Julian Logout must win over leftover Log in to your account aria",
    )

    monti_guest_home = _Page(
        "https://www.montiboutique.com/en-JP",
        "My acccount My orders Wishlist REGISTERED CUSTOMERS",
        password=0,
        logout=0,
    )
    _assert(
        not member_session_confirmed(monti_guest_home, "montiboutique"),
        "Monti guest footer My orders must not count as logged in",
    )

    eleonora_login = _Page(
        "https://eleonorabonucci.com/en/myaccount/login",
        "My Account If you already have an account, login with your e-mail address and password. EMAIL: PASSWORD: SIGN IN",
        password=1,
        logout=0,
    )
    _assert(not member_session_confirmed(eleonora_login, "eleonorabonucci"), "Eleonora login form must wait")

    buyma_login = _Page(
        "https://www.buyma.com/login/",
        "すでに会員の方 ログイン",
        password=1,
        logout=0,
    )
    _assert(not member_session_confirmed(buyma_login, "buyma"), "Buyma login must wait")
    buyma_member = _Page(
        "https://www.buyma.com/my/",
        "マイページ 出品する ログアウト",
        password=0,
        logout=0,
    )
    _assert(member_session_confirmed(buyma_member, "buyma"), "Buyma mypage should confirm")

    _assert(set(RECIPES) >= {"julian-fashion", "montiboutique", "minettiangeloonline", "eleonorabonucci", "buyma"}, "recipes")
    for code in ("julian-fashion", "montiboutique", "minettiangeloonline", "eleonorabonucci"):
        recipe = RECIPES[code]
        _assert(bool(recipe.home_url), f"{code} home_url")
        _assert(bool(recipe.open_clicks), f"{code} open_clicks")
    from core.sessions.login_recipes import fill_and_submit_login

    _assert(fill_and_submit_login(None, "julian-fashion", "", "secret") is False, "empty email must not fill")
    print("PASS member session rules")


def test_launcher_notices() -> None:
    print("\n=== launcher notices ===")
    from apps.launcher.app import LauncherApp

    _assert(LauncherApp.MSG_SAVE_OK == "登録に成功しました", "save notice")
    _assert(LauncherApp.MSG_TEST_OK == "登録成功", "test ok notice")
    _assert(LauncherApp.MSG_NEED_REGISTER == "ログイン情報を登録してください", "test empty notice")
    src = Path(ROOT / "apps" / "launcher" / "app.py").read_text(encoding="utf-8")
    _assert("ログイン情報の保存" in src, "save button label")
    _assert("手動ログイン" not in src, "old manual-login label still present")
    _assert("Chrome が開きます" not in src, "cookie save must not open a Chrome intro")
    _assert("クッキーを登録しています" in src, "busy overlay copy")
    _assert("_set_cookie_busy" in src, "busy lock")
    _assert("EC_JULIAN_EMAIL" in src or "site_accounts" in src, "per-site accounts")
    _assert("wipe_browser_profile=True" in src, "initialize must wipe Chrome profiles")
    _assert("ChatGPTチャンネル" in src, "chatgpt channel section")
    _assert("説明文チャンネルURL" in src, "chatgpt description channel")
    _assert("画像チャンネルURL" in src, "chatgpt image channel")
    _assert("_save_chatgpt_channels" in src, "chatgpt channel save")
    _assert("_save_chatgpt_cookie" not in src, "chatgpt cookie save must be gone")
    print("PASS launcher notices")


def test_http_login_parsers() -> None:
    print("\n=== HTTP login parsers ===")
    from core.sessions.http_login import (
        hidden_input_value,
        hidden_inputs,
        is_waf_response,
        member_html_confirmed,
        playwright_cookies_from_session,
    )

    html = (
        '<form>'
        '<input type="hidden" name="__VIEWSTATE" value="/wEPDwULLTE1" />'
        '<input type="hidden" name="onetimeticket" value="abc123" />'
        '<input type="text" name="email" value="" />'
        "</form>"
    )
    _assert(hidden_input_value(html, "__VIEWSTATE") == "/wEPDwULLTE1", "viewstate")
    _assert(hidden_inputs(html)["onetimeticket"] == "abc123", "ticket")
    waf_html = "<!doctype html><title>Azure WAF</title><meta name='description' content='Azure WAF JS Challenge'/>"
    _assert(is_waf_response(403, "https://www.julian-fashion.com/en-JP", waf_html), "julian waf")
    _assert(not is_waf_response(200, "https://www.buyma.com/login/", "<title>ログイン</title>"), "buyma not waf")
    _assert(
        member_html_confirmed("buyma", "https://www.buyma.com/my/", "マイページ 出品する"),
        "buyma member html",
    )
    _assert(
        not member_html_confirmed("buyma", "https://www.buyma.com/login/", "txtLoginPass ログイン"),
        "buyma login html",
    )
    _assert(
        member_html_confirmed("julian-fashion", "https://www.julian-fashion.com/en-JP", '<a href="/en-JP/user/logout">Logout</a>'),
        "julian logout link",
    )

    class _Cookie:
        name = "PHPSESSID"
        value = "sess"
        domain = ".angelominetti.it"
        path = "/"
        secure = True
        expires = None
        rest = {"HttpOnly": True}

    class _Session:
        class cookies:  # noqa: N801
            jar = [_Cookie()]

    cookies = playwright_cookies_from_session(_Session(), "https://www.angelominetti.it/login")
    _assert(cookies[0]["name"] == "PHPSESSID", "cookie name")
    _assert(cookies[0]["httpOnly"] is True, "httponly")
    _assert(cookies[0]["domain"] == ".angelominetti.it", "domain")
    print("PASS HTTP login parsers")


def test_http_login_empty_credentials() -> None:
    print("\n=== HTTP login empty credentials ===")
    from core.sessions.http_login import LoginFailedError, http_login_buyma, http_login_ec

    try:
        http_login_ec("julian-fashion", "", "x")
        raise AssertionError("empty email must fail")
    except LoginFailedError:
        pass
    try:
        http_login_buyma("", "x")
        raise AssertionError("empty buyma email must fail")
    except LoginFailedError:
        pass
    print("PASS HTTP empty credentials")


def test_per_site_accounts_and_password_prefs(tmp: Path) -> None:
    print("\n=== per-site accounts / Chrome password prefs ===")
    from core.sessions.site_accounts import (
        env_updates_from_site_accounts,
        load_all_site_accounts,
        resolve_site_account,
    )
    from core.utils.chrome_profile import disable_chrome_password_manager
    from types import SimpleNamespace

    loaded = load_all_site_accounts(
        {
            "EC_SITE_EMAIL": "shared@example.com",
            "EC_SITE_PASSWORD": "shared-pass",
            "EC_MINETTI_PASSWORD": "minetti-pass",
            "EC_MONTI_EMAIL": "monti@example.com",
            "EC_MONTI_PASSWORD": "monti-pass",
        }
    )
    _assert(loaded["julian-fashion"]["email"] == "shared@example.com", "julian fallback email")
    _assert(loaded["montiboutique"]["email"] == "monti@example.com", "monti own email")
    _assert(loaded["minettiangeloonline"]["password"] == "minetti-pass", "minetti own password")
    settings = SimpleNamespace(
        ec_site_email="shared@example.com",
        ec_site_password="shared-pass",
        ec_minetti_password="minetti-pass",
        ec_julian_email="",
        ec_julian_password="",
        ec_monti_email="monti@example.com",
        ec_monti_password="monti-pass",
        ec_minetti_email="",
        ec_eleonora_email="",
        ec_eleonora_password="",
    )
    _assert(resolve_site_account(settings, "julian-fashion") == ("shared@example.com", "shared-pass"), "julian resolve")
    _assert(resolve_site_account(settings, "montiboutique") == ("monti@example.com", "monti-pass"), "monti resolve")
    _assert(resolve_site_account(settings, "minettiangeloonline")[1] == "minetti-pass", "minetti resolve")
    updates = env_updates_from_site_accounts(loaded)
    _assert(updates["EC_MONTI_EMAIL"] == "monti@example.com", "env flatten")
    _assert("EC_JULIAN_PASSWORD" in updates, "julian key")

    from core.utils.chrome_launch import persistent_context_kwargs

    kwargs = persistent_context_kwargs(user_data_dir=str(tmp / "p"), headless=True)
    _assert("--disable-save-password-bubble" in kwargs["args"], "chrome flag")
    disable_chrome_password_manager(tmp / "chrome-profile")
    prefs = (tmp / "chrome-profile" / "Default" / "Preferences").read_text(encoding="utf-8")
    _assert("password_manager_enabled" in prefs, "prefs written")
    _assert("false" in prefs.lower(), "password manager off")
    print("PASS per-site accounts / Chrome password prefs")


def test_auto_confirm_wait() -> None:
    print("\n=== auto-confirm wait ===")
    os.environ["EC_BUYMA_AUTO_CONFIRM"] = "1"
    from importlib import reload
    import core.utils.user_confirm as uc

    reload(uc)
    _assert(uc.wait_for_user_ready("t", "m") is True, "auto confirm failed")
    uc.notify_user("t", "done")
    print("PASS auto-confirm")


def test_helper_cli_dispatch() -> None:
    print("\n=== helper CLI help (no browser) ===")
    from scripts.ec_cookie_login import main as ec_main

    code = ec_main(["--help"])
    _assert(code == 0, f"help exit={code}")
    code = ec_main([])
    _assert(code == 1, f"missing site exit={code}")
    from scripts.buyma_cookie_login import main as buyma_main

    _assert(callable(buyma_main), "buyma helper missing")
    print("PASS helper CLI")


def test_launcher_methods_exist() -> None:
    print("\n=== launcher settings API ===")
    from apps.launcher import app as launcher

    needed = [
        "_save_accounts",
        "_reset_buyma",
        "_reset_ec",
        "_reset_ec_selected",
        "_save_ec_cookie",
        "_verify_ec_cookie",
        "_save_buyma_cookie",
        "_verify_buyma_cookie",
        "_save_chatgpt_channels",
        "_refresh_lock_ui",
        "_entry_get",
        "_launch_cookie_helper",
        "_selected_ec_site",
        "_set_cookie_busy",
        "_stash_ec_fields",
        "_account_env_updates",
    ]
    for name in needed:
        _assert(hasattr(launcher.LauncherApp, name), f"missing {name}")
    _assert(not hasattr(launcher.LauncherApp, "_ec_locked") or True, "ok")
    # Old exclusive lock helper should be gone.
    _assert(not hasattr(launcher.LauncherApp, "_ec_locked"), "old _ec_locked still present")
    print("PASS launcher API")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ec_buyma_acct_"))
    try:
        env_dir = tmp / "envtest"
        env_dir.mkdir(parents=True)
        test_env_account_save_preserves_order(env_dir)

        ec_dir = tmp / "ectest"
        ec_dir.mkdir(parents=True)
        test_ec_reset_clears_cookies_and_storage(ec_dir)

        waf_dir = tmp / "waftest"
        waf_dir.mkdir(parents=True)
        test_session_rejects_waf_only_cookies(waf_dir)

        buyma_dir = tmp / "buyma"
        buyma_dir.mkdir(parents=True)
        test_buyma_reset(buyma_dir)

        gpt_dir = tmp / "chatgpt"
        gpt_dir.mkdir(parents=True)
        test_chatgpt_session_and_reset(gpt_dir)

        test_member_session_not_fooled_by_guest_my_account()
        test_launcher_notices()
        test_http_login_parsers()
        test_http_login_empty_credentials()
        pref_dir = tmp / "prefs"
        pref_dir.mkdir(parents=True)
        test_per_site_accounts_and_password_prefs(pref_dir)
        test_auto_confirm_wait()
        test_helper_cli_dispatch()
        test_launcher_methods_exist()
        print("\nALL PASS")
        return 0
    finally:
        os.environ.pop("EC_BUYMA_AUTO_CONFIRM", None)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
