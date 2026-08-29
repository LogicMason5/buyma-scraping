"""Load saved sessions and record member vs guest DOM markers (headed Chrome)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.sessions.login_recipes import member_session_confirmed, recipe_for
from core.utils.chrome_launch import launch_persistent_chrome, persistent_context_kwargs, wait_out_waf
from core.utils.chrome_profile import prepare_chrome_profile
from core.utils.playwright_cookies import add_cookies_resilient
from core.utils.playwright_runtime import acquire_playwright, release_playwright

OUT = ROOT / "workspace" / "member_markers.json"

SITES = {
    "julian-fashion": "https://www.julian-fashion.com/en-JP",
    "montiboutique": "https://www.montiboutique.com/en-JP",
    "minettiangeloonline": "https://www.angelominetti.it/",
    "eleonorabonucci": "https://eleonorabonucci.com/",
}

SESSION_CANDIDATES = [
    ROOT / "dist" / "EC-Buyma" / "secrets" / "ec_sessions",
    ROOT / "secrets" / "ec_sessions",
]


def _session_path(site: str) -> Path | None:
    for root in SESSION_CANDIDATES:
        path = root / site / "storage_state.json"
        if path.exists():
            return path
    return None


def _dump(page, site: str) -> dict:
    recipe = recipe_for(site)
    data = page.evaluate(
        """() => {
          const text = (document.body && document.body.innerText || '').slice(0, 1800);
          const links = Array.from(document.querySelectorAll('a, button')).map(el => ({
            tag: el.tagName, text: (el.innerText || el.value || '').trim().slice(0, 40),
            href: el.getAttribute('href') || '', aria: el.getAttribute('aria-label') || ''
          })).filter(x => /account|login|log in|sign|logout|log out|esci|mypage|マイ|password/i
            .test(x.text + ' ' + x.aria + ' ' + x.href)).slice(0, 30);
          const passwords = Array.from(document.querySelectorAll("input[type='password']"))
            .map(el => ({id: el.id, name: el.name, visible: !!(el.offsetWidth || el.offsetHeight)}));
          return {url: location.href, title: document.title, text, links, passwords};
        }"""
    )
    confirmed = False
    try:
        confirmed = member_session_confirmed(page, site)
    except Exception as exc:  # noqa: BLE001
        data["confirm_error"] = str(exc)
    data["member_session_confirmed"] = confirmed
    data["login_url"] = recipe.login_url if recipe else ""
    return data


def main() -> int:
    results: dict = {}
    pw = acquire_playwright()
    profile = ROOT / "workspace" / "tmp_member_probe_profile"
    prepare_chrome_profile(profile)
    context = None
    try:
        context = launch_persistent_chrome(
            pw,
            **persistent_context_kwargs(user_data_dir=str(profile), headless=False, maximized=False),
        )
        page = context.pages[0] if context.pages else context.new_page()
        for site, home in SITES.items():
            print(f"== {site}")
            rec: dict = {"home": home}
            session = _session_path(site)
            rec["session"] = str(session) if session else None
            recipe = recipe_for(site)
            try:
                page.goto(recipe.login_url if recipe else home, wait_until="domcontentloaded", timeout=60000)
                wait_out_waf(page, timeout_ms=25000)
                page.wait_for_timeout(2500)
                rec["guest_or_current_login"] = _dump(page, site)
            except Exception as exc:  # noqa: BLE001
                rec["guest_or_current_login"] = {"error": str(exc)}
            if session:
                try:
                    payload = json.loads(session.read_text(encoding="utf-8"))
                    cookies = payload.get("cookies") if isinstance(payload, dict) else payload
                    if isinstance(cookies, list):
                        add_cookies_resilient(context, cookies)
                    page.goto(home, wait_until="domcontentloaded", timeout=60000)
                    wait_out_waf(page, timeout_ms=25000)
                    page.wait_for_timeout(3000)
                    rec["with_saved_cookies_home"] = _dump(page, site)
                    if recipe:
                        page.goto(recipe.login_url, wait_until="domcontentloaded", timeout=60000)
                        wait_out_waf(page, timeout_ms=20000)
                        page.wait_for_timeout(2500)
                        rec["with_saved_cookies_login"] = _dump(page, site)
                except Exception as exc:  # noqa: BLE001
                    rec["with_saved_cookies_error"] = str(exc)
            results[site] = rec
            print(
                "   login_confirmed=",
                (rec.get("guest_or_current_login") or {}).get("member_session_confirmed"),
                " cookie_home=",
                (rec.get("with_saved_cookies_home") or {}).get("member_session_confirmed"),
            )
    finally:
        if context:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass
        release_playwright()
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
