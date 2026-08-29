"""Interact with sell-variation color/size + area using real classes."""

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
        page.goto(s.buyma_new_listing_url + "&_fresh=probe6", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        session._dismiss_onboarding()
        session._select_category("レディースファッション ジャケット・アウター ジャケット")
        page.wait_for_timeout(1500)

        var_html = page.locator(".sell-variation").first.inner_html()
        (s.workspace_dir / "buyma" / "sell_variation.html").write_text(var_html, encoding="utf-8")
        print("variation text:", page.locator(".sell-variation").first.inner_text()[:500])

        # Tabs
        tabs = page.evaluate(
            """() => Array.from(document.querySelectorAll('.sell-variation__tab-item')).map(e => ({
              text: (e.innerText||'').trim(), class: (e.className||'').toString(), active: e.className.includes('active') || e.getAttribute('aria-selected')
            }))"""
        )
        print("tabs:", tabs)

        # Click color option to open palette
        page.locator(".sell-color-option").first.click()
        page.wait_for_timeout(800)
        after_color_click = page.evaluate(
            """() => ({
              bodySlice: (() => {
                const t = (document.body.innerText||'').split('\\n').map(x=>x.trim()).filter(Boolean);
                const i = t.findIndex(x => x === '色・サイズ');
                return t.slice(i, i+40);
              })(),
              panels: Array.from(document.querySelectorAll('.sell-color-option__panel, [class*=color], .bmm-c-modal, #modal-root')).slice(0,20).map(e => ({
                class: (e.className||'').toString().slice(0,100),
                text: (e.innerText||'').trim().slice(0,100),
                visible: !!(e.offsetWidth && e.offsetHeight)
              })),
              modal: ((document.querySelector('#modal-root')||{}).innerText||'').slice(0,800)
            })"""
        )
        print("after color click:", json.dumps(after_color_click, ensure_ascii=False, indent=2)[:3500])
        page.screenshot(path=str(s.workspace_dir / "buyma" / "color_click.png"))

        # Try size tab
        page.locator(".sell-variation__tab-item").filter(has_text="サイズ").click()
        page.wait_for_timeout(800)
        size_html = page.locator(".sell-variation").first.inner_html()
        (s.workspace_dir / "buyma" / "sell_variation_size.html").write_text(size_html, encoding="utf-8")
        print("size variation text:", page.locator(".sell-variation").first.inner_text()[:800])

        # Look for size checkboxes/buttons inside variation
        size_controls = page.evaluate(
            """() => {
              const root = document.querySelector('.sell-variation');
              return {
                checks: Array.from(root.querySelectorAll('input,button,label,.Select,[class*=size]')).map(e => ({
                  tag: e.tagName, type: e.type||'', class:(e.className||'').toString().slice(0,100),
                  text: (e.innerText||'').trim().slice(0,40), placeholder: e.placeholder||'', value: e.value||''
                })).slice(0,50),
                text: (root.innerText||'').slice(0,800)
              };
            }"""
        )
        print("size controls:", json.dumps(size_controls, ensure_ascii=False, indent=2)[:4000])

        # Area: dump DOM around 買付地/発送地 with bmm radios
        area = page.evaluate(
            """() => {
              const findSection = (label) => {
                const heads = Array.from(document.querySelectorAll('.sell-heading, h2,h3,h4,div'));
                let anchor = null;
                for (const el of heads) {
                  const t = (el.innerText||'').trim().split('\\n')[0].trim().replace(/必須/g,'').trim();
                  if (t === label) { anchor = el; break; }
                }
                if (!anchor) return null;
                let root = anchor.parentElement;
                for (let i=0;i<8 && root;i++) {
                  if (root.querySelectorAll('input[type=radio], .bmm-c-radio, .Select').length) {
                    return {
                      class: (root.className||'').toString().slice(0,120),
                      html: root.innerHTML.slice(0,3000),
                      text: (root.innerText||'').slice(0,500),
                      radios: Array.from(root.querySelectorAll('input[type=radio]')).map(e => ({name:e.name,value:e.value,checked:e.checked})),
                      selects: Array.from(root.querySelectorAll('.Select')).map(e => (e.innerText||'').trim().slice(0,40))
                    };
                  }
                  root = root.parentElement;
                }
                return {err:'no controls', anchor: (anchor.outerHTML||'').slice(0,300)};
              };
              return {buy: findSection('買付地'), ship: findSection('発送地')};
            }"""
        )
        print("areas:", json.dumps(area, ensure_ascii=False, indent=2)[:5000])
        (s.workspace_dir / "buyma" / "area_sections.json").write_text(json.dumps(area, ensure_ascii=False, indent=2), encoding="utf-8")
        if area.get("ship") and area["ship"].get("html"):
            (s.workspace_dir / "buyma" / "ship_from_section.html").write_text(area["ship"]["html"], encoding="utf-8")
        if area.get("buy") and area["buy"].get("html"):
            (s.workspace_dir / "buyma" / "buy_area_section.html").write_text(area["buy"]["html"], encoding="utf-8")
    finally:
        session.close()


if __name__ == "__main__":
    main()
