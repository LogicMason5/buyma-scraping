"""Probe Buyma category / size / shipping controls on sell/new?tab=b."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.buyma.buyma_browser_service import BuymaBrowserSession
from core.config import clear_settings_cache, get_settings


def main() -> None:
    clear_settings_cache()
    settings = get_settings()
    session = BuymaBrowserSession()
    session.start()
    try:
        assert session.page is not None
        if not session.ensure_logged_in(timeout_seconds=45):
            raise SystemExit("not logged in")
        page = session.page
        page.goto(settings.buyma_new_listing_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        session._dismiss_onboarding()

        out: dict = {"steps": []}

        # --- Category ---
        for sel in ("text=選択してください", "button:has-text('選択してください')"):
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click()
                page.wait_for_timeout(800)
                out["steps"].append(f"clicked category opener via {sel}")
                break
        out["after_category_open"] = page.evaluate(
            """() => {
              const texts = Array.from(document.querySelectorAll('li,button,a,div[role=option],label'))
                .filter(e => e.offsetParent && (e.innerText||'').trim().length && (e.innerText||'').trim().length < 40)
                .map(e => (e.innerText||'').trim().split('\\n')[0])
                .slice(0, 80);
              return Array.from(new Set(texts));
            }"""
        )
        # try click レディースファッション
        for label in ("レディースファッション", "レディース"):
            loc = page.locator(f"text={label}").first
            try:
                if loc.count() and loc.is_visible(timeout=500):
                    loc.click()
                    page.wait_for_timeout(1000)
                    out["steps"].append(f"clicked {label}")
                    break
            except Exception as exc:  # noqa: BLE001
                out["steps"].append(f"fail {label}: {exc}")
        out["after_ladies"] = page.evaluate(
            """() => Array.from(new Set(
              Array.from(document.querySelectorAll('li,button,a,div[role=option],label'))
                .filter(e => e.offsetParent && (e.innerText||'').trim().length && (e.innerText||'').trim().length < 40)
                .map(e => (e.innerText||'').trim().split('\\n')[0])
            )).slice(0, 100)"""
        )

        # --- Size tab ---
        page.locator("text=サイズ").first.click()
        page.wait_for_timeout(800)
        out["size_ui"] = page.evaluate(
            """() => {
              const section = Array.from(document.querySelectorAll('h2,h3,h4,div,span'))
                .find(e => ((e.innerText||'').trim().split('\\n')[0]||'').includes('色・サイズ'));
              let root = section ? section.parentElement : document.body;
              for (let i=0;i<8 && root;i++) {
                if (root.querySelectorAll('input,button,select,td').length > 5) break;
                root = root.parentElement;
              }
              return {
                checkboxes: Array.from((root||document).querySelectorAll("input[type=checkbox],input[type=radio]"))
                  .slice(0,30).map(e => ({
                    type:e.type, text:(e.parentElement&&e.parentElement.innerText||'').trim().slice(0,40),
                    value:e.value, name:e.name
                  })),
                buttons: Array.from((root||document).querySelectorAll('button,a'))
                  .map(e => (e.innerText||'').trim()).filter(t => t && t.length < 30).slice(0,40),
                tableHeaders: Array.from((root||document).querySelectorAll('th'))
                  .map(e => (e.innerText||'').trim()).slice(0,20),
                selects: Array.from((root||document).querySelectorAll('select')).length,
              };
            }"""
        )

        # --- Shipping ---
        page.locator("text=配送方法を追加").first.click()
        page.wait_for_timeout(1000)
        out["shipping_ui"] = page.evaluate(
            """() => Array.from(new Set(
              Array.from(document.querySelectorAll('li,button,a,div,label,td'))
                .filter(e => e.offsetParent && /ヤマト|ゆう|パック|宅急|配送/.test(e.innerText||''))
                .map(e => (e.innerText||'').trim().replace(/\\s+/g,' ').slice(0,80))
            )).slice(0,40)"""
        )

        path = settings.workspace_dir / "buyma" / "listing_cat_size_ship_probe.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False, indent=2)[:5000])
        print("wrote", path)
        page.screenshot(path=str(settings.workspace_dir / "buyma" / "listing_cat_size_ship.png"), full_page=False)
    finally:
        session.close()


if __name__ == "__main__":
    main()
