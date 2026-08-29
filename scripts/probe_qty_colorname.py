"""Probe color name + quantity fields after color/size selection."""

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
        page.goto(s.buyma_new_listing_url + "&_fresh=qty", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        session._dismiss_onboarding()
        session._select_category("レディースファッション ジャケット・アウター ジャケット")
        page.wait_for_timeout(800)
        session._select_color_size(color="その他", size_text="指定なし")
        page.wait_for_timeout(800)

        dump = page.evaluate(
            """() => {
              const root = document.querySelector('.sell-variation');
              const inputs = Array.from((root||document).querySelectorAll('input,textarea,select')).map(e => ({
                type: e.type, name: e.name, disabled: e.disabled, value: e.value,
                placeholder: e.placeholder, class: (e.className||'').toString().slice(0,90),
                label: ((e.closest('label')||e.parentElement||{}).innerText||'').trim().replace(/\\s+/g,' ').slice(0,60)
              }));
              return {
                text: root ? root.innerText.slice(0,1200) : null,
                html: root ? root.innerHTML.slice(0,8000) : null,
                inputs,
                colorTable: (() => {
                  const t = document.querySelector('.sell-color-table');
                  return t ? {text: t.innerText.slice(0,500), html: t.innerHTML.slice(0,3000)} : null;
                })()
              };
            }"""
        )
        (s.workspace_dir / "buyma" / "qty_probe.json").write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
        (s.workspace_dir / "buyma" / "qty_probe.html").write_text(dump.get("html") or "", encoding="utf-8")
        print("text:", (dump.get("text") or "")[:800])
        print("inputs:", json.dumps(dump.get("inputs"), ensure_ascii=False, indent=2)[:4000])
        print("color table:", (dump.get("colorTable") or {}).get("text"))

        # Try fill color name
        page.locator(".sell-variation__tab-item", has_text="色").click()
        page.wait_for_timeout(400)
        color_inputs = page.evaluate(
            """() => Array.from(document.querySelectorAll('.sell-color-table input, .sell-variation input')).map(e => ({
              disabled: e.disabled, placeholder: e.placeholder, value: e.value, class: (e.className||'').toString().slice(0,80)
            }))"""
        )
        print("color inputs after tab", color_inputs)

        # Fill enabled text inputs in color table
        page.evaluate(
            """() => {
              const inputs = Array.from(document.querySelectorAll('.sell-color-table input[type=text], .sell-variation input[type=text]'));
              for (const inp of inputs) {
                if (inp.disabled) continue;
                if ((inp.placeholder||'').includes('色指定')) continue;
                const proto = window.HTMLInputElement.prototype;
                const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                if (desc && desc.set) desc.set.call(inp, 'その他');
                else inp.value = 'その他';
                inp.dispatchEvent(new Event('input', {bubbles:true}));
                inp.dispatchEvent(new Event('change', {bubbles:true}));
              }
            }"""
        )
        page.wait_for_timeout(400)

        # Quantity fields anywhere
        qty = page.evaluate(
            """() => {
              const labels = Array.from(document.querySelectorAll('p,label,th,div,span')).filter(e => /買付|数量|在庫/.test((e.innerText||'').trim()) && (e.innerText||'').trim().length < 30);
              return {
                labels: labels.map(e => e.innerText.trim()).slice(0,30),
                numberInputs: Array.from(document.querySelectorAll('input[type=number], input.bmm-c-text-field')).filter(e => /qty|quantity|stock|amount|数/.test((e.name||'')+(e.className||'')+(e.placeholder||'')) || true).slice(0,20).map(e => ({
                  name:e.name, type:e.type, value:e.value, placeholder:e.placeholder, class:(e.className||'').toString().slice(0,80),
                  near: ((e.closest('td,tr,div')||{}).innerText||'').trim().replace(/\\s+/g,' ').slice(0,80)
                }))
              };
            }"""
        )
        # Better: find section 買付できる
        qty2 = page.evaluate(
            """() => {
              const t = document.body.innerText;
              const i = t.indexOf('買付');
              return t.slice(Math.max(0,i-100), i+500);
            }"""
        )
        print("near 買付:", qty2[:600])

        # Look for quantity table after size
        page.locator(".sell-variation__tab-item", has_text="サイズ").click()
        page.wait_for_timeout(400)
        size_html = page.locator(".sell-variation").inner_html()
        (s.workspace_dir / "buyma" / "size_after.html").write_text(size_html, encoding="utf-8")
        print("size text", page.locator(".sell-variation").inner_text()[:600])

        # All inputs with small width that look like qty
        nums = page.evaluate(
            """() => Array.from(document.querySelectorAll('input')).map(e => {
              const r = e.getBoundingClientRect();
              return {type:e.type, value:e.value, name:e.name, w:Math.round(r.width), h:Math.round(r.height),
                placeholder:e.placeholder, class:(e.className||'').toString().slice(0,70),
                near:((e.closest('tr,td,div')||{}).innerText||'').trim().replace(/\\s+/g,' ').slice(0,50)};
            }).filter(e => e.w>0 && e.h>0 && (e.type==='text'||e.type==='number') && e.w < 120).slice(0,40)"""
        )
        print("small inputs", json.dumps(nums, ensure_ascii=False, indent=2)[:3500])
        page.screenshot(path=str(s.workspace_dir / "buyma" / "qty_probe.png"), full_page=True)
    finally:
        session.close()


if __name__ == "__main__":
    main()
