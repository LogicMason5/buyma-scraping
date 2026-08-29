"""Force-open area selects and force-check shipping boxes."""

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
        page.goto(s.buyma_new_listing_url + "&_fresh=dbg2", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        session._dismiss_onboarding()

        page.locator("label.bmm-c-radio:has(input[name='purchaseArea-region'][value='overseas'])").click()
        page.wait_for_timeout(800)

        # Try multiple open strategies
        for strategy in ("arrow", "control", "value", "input_focus"):
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            opened = page.evaluate(
                """(strategy) => {
                  const h = Array.from(document.querySelectorAll('.bmm-c-summary__ttl')).find(e => e.innerText.trim()==='買付地');
                  const root = h.closest('.bmm-c-panel__item');
                  const sel = root.querySelectorAll('.Select')[0];
                  if (!sel) return {ok:false};
                  const target =
                    strategy === 'arrow' ? sel.querySelector('.Select-arrow-zone, .Select-arrow') :
                    strategy === 'control' ? sel.querySelector('.Select-control') :
                    strategy === 'value' ? sel.querySelector('.Select-value, .Select-value-label') :
                    sel.querySelector('.Select-input');
                  if (!target) return {ok:false, missing:true};
                  target.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                  target.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                  target.click();
                  return {ok:true, class: sel.className, isOpen: sel.classList.contains('is-open')};
                }""",
                strategy,
            )
            page.wait_for_timeout(500)
            opts = page.evaluate(
                "() => Array.from(document.querySelectorAll('.Select-option')).map(e => e.innerText.trim()).slice(0,30)"
            )
            print(strategy, opened, "opts", opts[:15], "count", len(opts))
            if opts:
                # pick ヨーロッパ if present
                page.evaluate(
                    """() => {
                      const o = Array.from(document.querySelectorAll('.Select-option')).find(e => e.innerText.includes('ヨーロッパ'));
                      if (o) o.click();
                    }"""
                )
                page.wait_for_timeout(600)
                break

        # Second select for Italy
        page.evaluate(
            """() => {
              const h = Array.from(document.querySelectorAll('.bmm-c-summary__ttl')).find(e => e.innerText.trim()==='買付地');
              const root = h.closest('.bmm-c-panel__item');
              const sel = root.querySelectorAll('.Select')[1];
              const arrow = sel && sel.querySelector('.Select-arrow-zone');
              if (arrow) {
                arrow.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                arrow.click();
              }
            }"""
        )
        page.wait_for_timeout(500)
        opts2 = page.evaluate(
            "() => Array.from(document.querySelectorAll('.Select-option')).map(e => e.innerText.trim()).slice(0,40)"
        )
        print("L2 opts", opts2[:20])
        page.evaluate(
            """() => {
              const o = Array.from(document.querySelectorAll('.Select-option')).find(e => e.innerText.trim()==='イタリア');
              if (o) o.click();
            }"""
        )
        page.wait_for_timeout(400)

        buy_txt = page.evaluate(
            """() => {
              const h = Array.from(document.querySelectorAll('.bmm-c-summary__ttl')).find(e => e.innerText.trim()==='買付地');
              return h.closest('.bmm-c-panel__item').innerText.slice(0,300);
            }"""
        )
        print("buy text", buy_txt)

        # Ship from domestic Aichi
        page.locator("label.bmm-c-radio:has(input[name='shippingArea-region'][value='domestic'])").click()
        page.wait_for_timeout(700)
        page.evaluate(
            """() => {
              const h = Array.from(document.querySelectorAll('.bmm-c-summary__ttl')).find(e => e.innerText.trim()==='発送地');
              const root = h.closest('.bmm-c-panel__item');
              const arrow = root.querySelector('.Select-arrow-zone');
              if (arrow) {
                arrow.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                arrow.click();
              }
            }"""
        )
        page.wait_for_timeout(500)
        prefs = page.evaluate(
            "() => Array.from(document.querySelectorAll('.Select-option')).map(e => e.innerText.trim())"
        )
        print("prefs sample", prefs[:15], "hasAichi", "愛知県" in prefs)
        page.evaluate(
            """() => {
              const o = Array.from(document.querySelectorAll('.Select-option')).find(e => e.innerText.trim()==='愛知県');
              if (o) o.click();
            }"""
        )
        page.wait_for_timeout(400)

        # Shipping checkbox: try input click / mark / row / set checked+events
        results = page.evaluate(
            """() => {
              const out = [];
              const rows = Array.from(document.querySelectorAll('.bmm-c-form-table__body tr')).filter(tr => (tr.innerText||'').includes('ヤマト'));
              if (!rows.length) return [{err:'no rows'}];
              const tr = rows[0];
              const input = tr.querySelector('input[type=checkbox]');
              const mark = tr.querySelector('.bmm-c-checkbox__mark');
              // strategy 1: input.click
              if (input) { input.click(); out.push({s:'input.click', checked: input.checked}); }
              if (input && !input.checked) {
                // strategy 2: mark click
                if (mark) { mark.click(); out.push({s:'mark.click', checked: input.checked}); }
              }
              if (input && !input.checked) {
                // strategy 3: set + events
                input.checked = true;
                input.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                input.dispatchEvent(new Event('input', {bubbles:true}));
                input.dispatchEvent(new Event('change', {bubbles:true}));
                out.push({s:'set+events', checked: input.checked});
              }
              if (input && !input.checked) {
                // strategy 4: click td
                tr.querySelector('td')?.click();
                out.push({s:'td.click', checked: input.checked});
              }
              return out;
            }"""
        )
        print("checkbox strategies", results)
        page.wait_for_timeout(300)
        # Playwright force on first yamato checkbox in shipping panel
        try:
            page.locator(".bmm-c-panel__item:has(.bmm-c-summary__ttl:text('配送方法')) input[type=checkbox]").first.click(force=True)
            page.wait_for_timeout(300)
        except Exception as exc:
            print("pw force fail", exc)
        checked = page.evaluate(
            """() => {
              const h = Array.from(document.querySelectorAll('.bmm-c-summary__ttl')).find(e => e.innerText.trim()==='配送方法');
              const root = h && h.closest('.bmm-c-panel__item');
              return Array.from((root||document).querySelectorAll('input[type=checkbox]')).map(e => e.checked);
            }"""
        )
        print("final checked", checked)
        page.screenshot(path=str(s.workspace_dir / "buyma" / "dbg_force.png"), full_page=True)
    finally:
        session.close()


if __name__ == "__main__":
    main()
