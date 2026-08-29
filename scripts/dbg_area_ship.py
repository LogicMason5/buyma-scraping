"""Debug area select menus + shipping checkbox visibility."""

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
        session.ensure_logged_in(timeout_seconds=45)
        page.goto(s.buyma_new_listing_url + "&_fresh=dbg", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        session._dismiss_onboarding()

        # Areas
        page.locator("label.bmm-c-radio:has(input[name='purchaseArea-region'][value='overseas'])").click()
        page.wait_for_timeout(800)
        html = page.evaluate(
            """() => {
              const h = Array.from(document.querySelectorAll('.bmm-c-summary__ttl')).find(e => e.innerText.trim()==='買付地');
              const root = h && h.closest('.bmm-c-panel__item');
              return root ? root.innerHTML.slice(0,4000) : null;
            }"""
        )
        (s.workspace_dir / "buyma" / "buy_after_radio.html").write_text(html or "", encoding="utf-8")
        selects = page.evaluate(
            """() => {
              const h = Array.from(document.querySelectorAll('.bmm-c-summary__ttl')).find(e => e.innerText.trim()==='買付地');
              const root = h && h.closest('.bmm-c-panel__item');
              return Array.from((root||document).querySelectorAll('.Select')).map((e,i) => ({
                i, class: e.className, text: e.innerText.trim().slice(0,40),
                html: e.outerHTML.slice(0,500)
              }));
            }"""
        )
        print("buy selects", json.dumps(selects, ensure_ascii=False, indent=2)[:3000])

        # Click first select control via JS and dump menu
        page.evaluate(
            """() => {
              const h = Array.from(document.querySelectorAll('.bmm-c-summary__ttl')).find(e => e.innerText.trim()==='買付地');
              const root = h.closest('.bmm-c-panel__item');
              const c = root.querySelector('.Select-control');
              c && c.click();
            }"""
        )
        page.wait_for_timeout(700)
        menu = page.evaluate(
            """() => ({
              open: !!document.querySelector('.Select.is-open, .Select-menu, .Select-menu-outer'),
              menus: Array.from(document.querySelectorAll('.Select-menu-outer, .Select-menu, .Select-option')).map(e => ({
                class: e.className.toString().slice(0,80),
                text: e.innerText.trim().slice(0,80)
              })).slice(0,40),
              allOptions: Array.from(document.querySelectorAll('.Select-option')).map(e => e.innerText.trim())
            })"""
        )
        print("menu", json.dumps(menu, ensure_ascii=False, indent=2)[:4000])

        # Try keyboard on focused select
        page.keyboard.type("ヨーロ")
        page.wait_for_timeout(500)
        menu2 = page.evaluate(
            """() => Array.from(document.querySelectorAll('.Select-option')).map(e => e.innerText.trim())"""
        )
        print("after type", menu2)

        # Shipping checkboxes visibility
        ship = page.evaluate(
            """() => Array.from(document.querySelectorAll('.bmm-c-form-table__body tr')).map((tr,i) => {
              const lab = tr.querySelector('label.bmm-c-checkbox');
              const rect = lab ? lab.getBoundingClientRect() : null;
              return {
                i,
                text: tr.innerText.replace(/\\s+/g,' ').slice(0,80),
                hasLab: !!lab,
                display: lab ? getComputedStyle(lab).display : null,
                visibility: lab ? getComputedStyle(lab).visibility : null,
                w: rect && rect.width, h: rect && rect.height,
                pointer: lab ? lab.className : null
              };
            })"""
        )
        print("ship rows", json.dumps(ship, ensure_ascii=False, indent=2)[:3000])

        # Click checkbox mark via JS
        clicked = page.evaluate(
            """() => {
              const rows = Array.from(document.querySelectorAll('.bmm-c-form-table__body tr'));
              for (const tr of rows) {
                if (!(tr.innerText||'').includes('ヤマト')) continue;
                const input = tr.querySelector('input[type=checkbox]');
                const lab = tr.querySelector('label.bmm-c-checkbox');
                if (lab) {
                  lab.click();
                  return {via:'label', checked: input && input.checked};
                }
              }
              return null;
            }"""
        )
        print("js click", clicked)
        page.wait_for_timeout(400)
        checked = page.evaluate(
            "() => !!document.querySelector('.bmm-c-form-table__body input[type=checkbox]:checked')"
        )
        print("checked", checked)

        page.screenshot(path=str(s.workspace_dir / "buyma" / "dbg_area_ship.png"), full_page=True)
    finally:
        session.close()


if __name__ == "__main__":
    main()
