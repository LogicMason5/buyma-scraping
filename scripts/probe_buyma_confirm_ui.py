"""Confirm size options + area cascade + shipping row selection."""

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
    s = get_settings()
    session = BuymaBrowserSession()
    session.start()
    try:
        assert session.page is not None
        page = session.page
        if not session.ensure_logged_in(timeout_seconds=45):
            raise SystemExit("not logged in")
        page.goto(s.buyma_new_listing_url + "&_fresh=probe7", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        session._dismiss_onboarding()
        session._select_category("レディースファッション ジャケット・アウター ジャケット")
        page.wait_for_timeout(1000)

        # Color: open + pick black
        page.locator(".sell-color-option").first.click()
        page.wait_for_timeout(400)
        page.locator(".sell-color-option__name", has_text="ブラック（黒）系").click()
        page.wait_for_timeout(600)
        print("color after:", page.locator(".sell-color-table").inner_text()[:200])

        # Size tab + open select inside variation only
        page.locator(".sell-variation__tab-item", has_text="サイズ").click()
        page.wait_for_timeout(500)
        page.locator(".sell-variation .Select .Select-control").first.click()
        page.wait_for_timeout(600)
        opts = page.evaluate(
            """() => Array.from(document.querySelectorAll('.Select-option')).map(e => (e.innerText||'').trim())"""
        )
        print("size options:", opts)
        # pick フリー or first
        pick = next((o for o in opts if o in {"フリー", "F", "ONESIZE", "ONE SIZE"}), opts[0] if opts else None)
        if pick:
            page.locator(".Select-option", has_text=pick).first.click()
            page.wait_for_timeout(500)
            print("picked", pick, "variation:", page.locator(".sell-variation").inner_text()[:300])

        # Areas
        page.locator("input[name='purchaseArea-region'][value='overseas']").check(force=True)
        page.wait_for_timeout(800)
        buy_after = page.evaluate(
            """() => {
              const t = (document.body.innerText||'').split('\\n').map(x=>x.trim()).filter(Boolean);
              const i = t.findIndex(x => x === '買付地');
              return {
                slice: t.slice(i, i+30),
                selects: Array.from(document.querySelectorAll('.Select')).map(e => (e.innerText||'').trim().split('\\n')[0]).slice(0,15)
              };
            }"""
        )
        print("buy after overseas:", json.dumps(buy_after, ensure_ascii=False, indent=2)[:2000])

        page.locator("input[name='shippingArea-region'][value='domestic']").check(force=True)
        page.wait_for_timeout(800)
        ship_after = page.evaluate(
            """() => {
              const t = (document.body.innerText||'').split('\\n').map(x=>x.trim()).filter(Boolean);
              const i = t.findIndex(x => x === '発送地');
              return {
                slice: t.slice(i, i+30),
                selects: Array.from(document.querySelectorAll('.Select')).map(e => (e.innerText||'').trim().split('\\n')[0])
              };
            }"""
        )
        print("ship after domestic:", json.dumps(ship_after, ensure_ascii=False, indent=2)[:2000])

        # Open last empty select near shipping and list prefs
        page.evaluate(
            """() => {
              // Find selects under shippingArea / near 発送地
              const heads = Array.from(document.querySelectorAll('.bmm-c-summary__ttl'));
              let shipRoot = null;
              for (const h of heads) {
                if ((h.innerText||'').trim() === '発送地') {
                  shipRoot = h.closest('.bmm-c-panel__item') || h.parentElement?.parentElement?.parentElement;
                  break;
                }
              }
              if (!shipRoot) return null;
              const sel = shipRoot.querySelector('.Select .Select-control');
              if (sel) sel.click();
              return shipRoot.innerText.slice(0,300);
            }"""
        )
        page.wait_for_timeout(500)
        pref_opts = page.evaluate(
            """() => Array.from(document.querySelectorAll('.Select-option')).map(e => (e.innerText||'').trim()).slice(0,60)"""
        )
        print("pref opts:", pref_opts)
        if "愛知県" in pref_opts:
            page.locator(".Select-option", has_text="愛知県").click()
            page.wait_for_timeout(500)
            print("selected 愛知県")

        # Cascading buy area: ヨーロッパ -> イタリア
        page.evaluate(
            """() => {
              const heads = Array.from(document.querySelectorAll('.bmm-c-summary__ttl'));
              for (const h of heads) {
                if ((h.innerText||'').trim() === '買付地') {
                  const root = h.closest('.bmm-c-panel__item');
                  const controls = root ? Array.from(root.querySelectorAll('.Select .Select-control')) : [];
                  if (controls[0]) controls[0].click();
                  return controls.length;
                }
              }
              return 0;
            }"""
        )
        page.wait_for_timeout(500)
        buy_opts = page.evaluate(
            """() => Array.from(document.querySelectorAll('.Select-option')).map(e => (e.innerText||'').trim()).slice(0,40)"""
        )
        print("buy level1 opts:", buy_opts)

        # Shipping section HTML dump
        ship_sec = page.evaluate(
            """() => {
              const heads = Array.from(document.querySelectorAll('.bmm-c-summary__ttl, h2,h3'));
              for (const h of heads) {
                if ((h.innerText||'').trim().replace(/必須/g,'').trim() === '配送方法') {
                  let root = h.closest('.bmm-c-panel__item') || h.parentElement;
                  for (let i=0;i<8 && root;i++) {
                    if ((root.innerText||'').includes('配送方法を追加')) {
                      return {html: root.innerHTML.slice(0,5000), text: root.innerText.slice(0,800),
                        inputs: Array.from(root.querySelectorAll('input')).map(e => ({type:e.type,name:e.name,value:e.value,checked:e.checked}))};
                    }
                    root = root.parentElement;
                  }
                }
              }
              return null;
            }"""
        )
        print("ship inputs:", (ship_sec or {}).get("inputs"))
        print("ship text:", ((ship_sec or {}).get("text") or "")[:500])
        (s.workspace_dir / "buyma" / "ship_sec.html").write_text((ship_sec or {}).get("html") or "", encoding="utf-8")

        page.screenshot(path=str(s.workspace_dir / "buyma" / "probe_confirm_ui.png"), full_page=True)
    finally:
        session.close()


if __name__ == "__main__":
    main()
