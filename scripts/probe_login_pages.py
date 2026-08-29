"""Probe EC/Buyma login pages (structure only; no credentials)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.scrapers.sites import SCRAPER_REGISTRY
from core.utils.chrome_launch import launch_persistent_chrome, persistent_context_kwargs, wait_out_waf
from core.utils.chrome_profile import prepare_chrome_profile
from core.utils.playwright_runtime import acquire_playwright, release_playwright

OUT = ROOT / "workspace" / "login_probe.json"

SITES = {
    "julian-fashion": "https://www.julian-fashion.com/en-JP/user/login",
    "montiboutique": "https://www.montiboutique.com/en-JP/user/login",
    "minettiangeloonline": "https://www.angelominetti.it/login",
    "eleonorabonucci": "https://eleonorabonucci.com/en/myaccount/login",
    "buyma": "https://www.buyma.com/login/",
}


def _probe_page(page, url: str) -> dict:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    wait_out_waf(page, timeout_ms=25000)
    page.wait_for_timeout(2500)
    data = page.evaluate(
        """() => {
          const vis = (el) => {
            if (!el) return false;
            const s = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.display !== 'none' && s.visibility !== 'hidden' && r.width + r.height > 0;
          };
          const inputs = Array.from(document.querySelectorAll('input')).filter(vis).map(el => ({
            type: el.type, name: el.name || '', id: el.id || '',
            placeholder: el.placeholder || '', autocomplete: el.autocomplete || ''
          }));
          const buttons = Array.from(document.querySelectorAll('button, input[type=submit], a')).filter(vis)
            .map(el => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim())
            .filter(t => t && t.length < 40)
            .slice(0, 40);
          const links = Array.from(document.querySelectorAll('a')).filter(vis)
            .map(el => ({text: (el.innerText || '').trim().slice(0, 40), href: el.href || '',
                         aria: el.getAttribute('aria-label') || ''}))
            .filter(x => /account|login|log in|sign|logout|log out|esci|mypage|マイ/i.test(x.text + x.aria + x.href))
            .slice(0, 25);
          const body = (document.body && document.body.innerText || '').slice(0, 1200);
          return {url: location.href, title: document.title, inputs, buttons, links, body};
        }"""
    )
    return data


def main() -> int:
    results = {}
    pw = acquire_playwright()
    profile = ROOT / "workspace" / "tmp_login_probe_profile"
    prepare_chrome_profile(profile)
    context = None
    try:
        context = launch_persistent_chrome(
            pw,
            **persistent_context_kwargs(user_data_dir=str(profile), headless=True, maximized=False),
        )
        page = context.pages[0] if context.pages else context.new_page()
        for code, url in SITES.items():
            print(f"== {code} {url}")
            try:
                data = _probe_page(page, url)
                results[code] = {
                    "ok": True,
                    "final_url": data.get("url"),
                    "title": data.get("title"),
                    "password_inputs": [i for i in data.get("inputs", []) if i.get("type") == "password"],
                    "email_inputs": [
                        i
                        for i in data.get("inputs", [])
                        if "email" in (i.get("type") + i.get("name") + i.get("id")).lower()
                    ],
                    "inputs": data.get("inputs"),
                    "buttons": data.get("buttons"),
                    "auth_links": data.get("links"),
                    "body_preview": (data.get("body") or "")[:800],
                }
                print("  url", results[code]["final_url"])
                print("  pass", results[code]["password_inputs"])
                print("  email", results[code]["email_inputs"])
                print("  buttons", results[code]["buttons"][:12])
            except Exception as exc:  # noqa: BLE001
                results[code] = {"ok": False, "error": str(exc)}
                print("  ERR", exc)
        scraper_login = {}
        for code, cls in SCRAPER_REGISTRY.items():
            s = cls()
            scraper_login[code] = {"base": s.base_url, "login": s.login_url}
        results["_scraper_urls"] = scraper_login
    finally:
        if context:
            context.close()
        release_playwright()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
