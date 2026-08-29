"""Fill color name + stock quantity and dump stock section DOM."""

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
        page.goto(s.buyma_new_listing_url + "&_fresh=stock", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        session._dismiss_onboarding()
        session._select_category("レディースファッション ジャケット・アウター ジャケット")
        page.wait_for_timeout(600)
        session._select_color_size(color="その他", size_text="指定なし")
        page.wait_for_timeout(600)

        # Color name on color tab
        page.locator(".sell-variation__tab-item", has_text="色").click()
        page.wait_for_timeout(400)
        color_html = page.evaluate(
            """() => {
              const t = document.querySelector('.sell-color-table');
              return t ? t.innerHTML.slice(0,4000) : 'none';
            }"""
        )
        (s.workspace_dir / "buyma" / "color_table_named.html").write_text(color_html, encoding="utf-8")
        print("color table text:", page.locator(".sell-color-table").inner_text()[:300] if page.locator(".sell-color-table").count() else "missing")

        # Fill first enabled text in color table
        filled = page.evaluate(
            """() => {
              const inputs = Array.from(document.querySelectorAll('.sell-color-table input[type=text]'));
              const inp = inputs.find(e => !e.disabled);
              if (!inp) return {ok:false, count: inputs.length};
              inp.focus();
              inp.click();
              const proto = window.HTMLInputElement.prototype;
              const desc = Object.getOwnPropertyDescriptor(proto, 'value');
              if (desc && desc.set) desc.set.call(inp, 'その他');
              else inp.value = 'その他';
              inp.dispatchEvent(new Event('input', {bubbles:true}));
              inp.dispatchEvent(new Event('change', {bubbles:true}));
              return {ok:true, value: inp.value, class: inp.className};
            }"""
        )
        print("color name fill", filled)
        # also playwright fill
        loc = page.locator(".sell-color-table input[type=text]:not([disabled])")
        if loc.count():
            loc.first.fill("その他")
            print("pw fill value", loc.first.input_value())

        # Stock section
        stock = page.evaluate(
            """() => {
              const heads = Array.from(document.querySelectorAll('p,h2,h3,h4,div,span'));
              let anchor = null;
              for (const el of heads) {
                const t = (el.innerText||'').trim().split('\\n')[0];
                if (t.includes('販売可否') || t.includes('在庫の設定') || t === 'すべての色') { anchor = el; break; }
              }
              // find by text 買付できる合計数量
              const all = Array.from(document.querySelectorAll('*'));
              let qtyLabel = null;
              for (const el of all) {
                if ((el.childNodes.length) && Array.from(el.childNodes).some(n => n.nodeType===3 && (n.textContent||'').includes('買付できる合計数量'))) {
                  qtyLabel = el; break;
                }
              }
              const stockRoot = (qtyLabel && qtyLabel.closest('.bmm-c-panel__item, .bmm-c-panel, section, div')) || null;
              return {
                qtyLabel: qtyLabel ? qtyLabel.innerText.slice(0,100) : null,
                nearbyHtml: qtyLabel ? qtyLabel.parentElement.innerHTML.slice(0,3000) : null,
                inputsNear: qtyLabel ? Array.from(qtyLabel.closest('.bmm-c-panel__item, .bmm-c-field, div')?.querySelectorAll('input') || []).map(e => ({
                  type:e.type, value:e.value, disabled:e.disabled, class:(e.className||'').toString().slice(0,80),
                  near:((e.parentElement||{}).innerText||'').trim().slice(0,40)
                })) : [],
                buyableButtons: Array.from(document.querySelectorAll('button,label,a,div,span')).filter(e => (e.innerText||'').trim()==='買付可').slice(0,5).map(e => ({
                  tag:e.tagName, class:(e.className||'').toString().slice(0,80)
                }))
              };
            }"""
        )
        print("stock", json.dumps(stock, ensure_ascii=False, indent=2)[:4000])
        if stock.get("nearbyHtml"):
            (s.workspace_dir / "buyma" / "stock_near.html").write_text(stock["nearbyHtml"], encoding="utf-8")

        # Click 買付可 and fill qty
        page.evaluate(
            """() => {
              const el = Array.from(document.querySelectorAll('button,label,a,div,span,td')).find(e => (e.innerText||'').trim()==='買付可');
              if (el) el.click();
            }"""
        )
        page.wait_for_timeout(500)

        # Fill any empty number-like fields near stock
        page.evaluate(
            """() => {
              // Look for input near 買付できる合計数量
              const nodes = Array.from(document.querySelectorAll('input[type=text], input[type=number]'));
              for (const inp of nodes) {
                const ctx = ((inp.closest('tr,div,section')||{}).innerText||'');
                if (!/買付できる合計|買付可|在庫/.test(ctx)) continue;
                if (inp.disabled) continue;
                inp.focus();
                const proto = window.HTMLInputElement.prototype;
                const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                if (desc && desc.set) desc.set.call(inp, '1');
                else inp.value = '1';
                inp.dispatchEvent(new Event('input', {bubbles:true}));
                inp.dispatchEvent(new Event('change', {bubbles:true}));
              }
            }"""
        )
        page.wait_for_timeout(400)

        # Playwright: get_by_text 買付できる合計数量を入力 then nearby input
        try:
            page.get_by_text("買付できる合計数量を入力", exact=False).click(timeout=2000)
            page.keyboard.type("1")
        except Exception as exc:
            print("qty type fail", exc)

        after = page.evaluate(
            """() => {
              const t = document.body.innerText;
              const i = t.indexOf('販売可否');
              return {
                slice: t.slice(i, i+450),
                errors: [...new Set((t.match(/[^\\n]*(色名称|数量|買付できる)[^\\n]*/g)||[]))].slice(0,10)
              };
            }"""
        )
        print("after", json.dumps(after, ensure_ascii=False, indent=2))
        page.screenshot(path=str(s.workspace_dir / "buyma" / "stock_fill.png"), full_page=True)
    finally:
        session.close()


if __name__ == "__main__":
    main()
