"""Probe Select controls for areas + size tab contents."""

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
        page.goto(s.buyma_new_listing_url + "&_fresh=probe3", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        session._dismiss_onboarding()
        session._select_category("レディースファッション ジャケット・アウター ジャケット")
        page.wait_for_timeout(1500)

        # Dump Select / area controls
        selects = page.evaluate(
            """() => {
              const out = [];
              for (const el of document.querySelectorAll('.Select, .Select-control, .Select-input, [class*=Select]')) {
                const rect = el.getBoundingClientRect();
                if (rect.width < 5 || rect.height < 5) continue;
                let label = '';
                let p = el.parentElement;
                for (let i=0;i<8 && p;i++) {
                  const h = p.querySelector('h2,h3,h4,label,.bmm-c-form-label');
                  if (h) { label = (h.innerText||'').trim().split('\\n')[0].slice(0,40); break; }
                  p = p.parentElement;
                }
                out.push({
                  tag: el.tagName,
                  class: (el.className||'').toString().slice(0,100),
                  text: (el.innerText||'').trim().slice(0,60),
                  placeholder: (el.querySelector('input')||{}).placeholder || '',
                  y: Math.round(rect.top),
                  label
                });
                if (out.length > 40) break;
              }
              // radios near 国内/海外
              const radios = Array.from(document.querySelectorAll('input[type=radio]')).map(e => ({
                name: e.name, value: e.value, checked: e.checked,
                label: ((e.closest('label')||e.parentElement||{}).innerText||'').trim().slice(0,40)
              }));
              return {selects: out, radios};
            }"""
        )
        print("=== SELECTS ===")
        print(json.dumps(selects, ensure_ascii=False, indent=2)[:5000])

        # Click size tab inside color/size: look for button/tab with exact サイズ near 色
        page.evaluate(
            """() => {
              const nodes = Array.from(document.querySelectorAll('button, a, li, div, span'));
              for (const el of nodes) {
                const t = (el.innerText||'').trim();
                if (t !== 'サイズ') continue;
                // prefer small tab-like nodes
                if (el.children.length > 3) continue;
                el.click();
                return true;
              }
              return false;
            }"""
        )
        page.wait_for_timeout(1000)
        size_ui = page.evaluate(
            """() => {
              const texts = (document.body.innerText||'').split('\\n').map(t=>t.trim()).filter(Boolean);
              const i = texts.findIndex(t => t === '色・サイズ');
              const slice = texts.slice(i, i+50);
              // any size-looking checkboxes/labels
              const labels = Array.from(document.querySelectorAll('label, .bmm-c-checkbox, [class*=size]')).map(e => (e.innerText||'').trim().replace(/\\s+/g,' ').slice(0,60)).filter(t => t && t.length < 40);
              const uniq = [...new Set(labels)].slice(0,80);
              const htmlBits = Array.from(document.querySelectorAll('[class*=size], [class*=Size], [class*=color], [class*=Color]')).slice(0,30).map(e => ({
                class: (e.className||'').toString().slice(0,80),
                text: (e.innerText||'').trim().slice(0,50)
              }));
              return {slice, uniq, htmlBits};
            }"""
        )
        print("=== SIZE UI ===")
        print(json.dumps(size_ui, ensure_ascii=False, indent=2)[:5000])

        # Open color placeholder and list options
        page.locator("input[placeholder='色指定なし']").click()
        page.wait_for_timeout(800)
        color_opts = page.evaluate(
            """() => {
              const opts = Array.from(document.querySelectorAll('[role=option], .Select-option, li, button, div')).map(e => (e.innerText||'').trim()).filter(t => t && t.length < 20);
              // common color words
              const colors = opts.filter(t => /黒|白|赤|青|緑|灰|茶|ベージ|紺|ピンク|その他|系統|ブラック|ホワイト|ブラウン|グレー|ネイビー/.test(t));
              return {colors: [...new Set(colors)].slice(0,40), sample: [...new Set(opts)].slice(0,60)};
            }"""
        )
        print("=== COLOR OPTS ===")
        print(json.dumps(color_opts, ensure_ascii=False, indent=2)[:3000])

        # Area: find Select near 発送地 and click
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        area = page.evaluate(
            """() => {
              const heads = Array.from(document.querySelectorAll('h2,h3,h4,div,span,label'));
              let anchor = null;
              for (const el of heads) {
                const t = (el.innerText||'').trim().split('\\n')[0].trim().replace(/必須/g,'').trim();
                if (t === '発送地') { anchor = el; break; }
              }
              if (!anchor) return {err:'no anchor'};
              let root = anchor.parentElement;
              for (let i=0;i<10 && root;i++) {
                const html = root.innerHTML;
                if (html.includes('国内') && html.includes('海外') && root.querySelectorAll('input,button,.Select').length) {
                  return {
                    depth: i,
                    text: (root.innerText||'').slice(0,400),
                    html: root.innerHTML.slice(0,2500),
                    radios: Array.from(root.querySelectorAll('input')).map(e => ({type:e.type,name:e.name,value:e.value,checked:e.checked,class:(e.className||'').toString().slice(0,60)})),
                    buttons: Array.from(root.querySelectorAll('button,a,label,span')).map(e => (e.innerText||'').trim()).filter(t=>t&&t.length<20).slice(0,30)
                  };
                }
                root = root.parentElement;
              }
              return {err:'not found'};
            }"""
        )
        print("=== AREA ROOT ===")
        print(json.dumps(area, ensure_ascii=False, indent=2)[:5000])

        # Try click 国内 via label/radio in that section then dump again
        page.evaluate(
            """() => {
              const heads = Array.from(document.querySelectorAll('h2,h3,h4,div,span,label'));
              let anchor = null;
              for (const el of heads) {
                const t = (el.innerText||'').trim().split('\\n')[0].trim().replace(/必須/g,'').trim();
                if (t === '発送地') { anchor = el; break; }
              }
              let root = anchor && anchor.parentElement;
              for (let i=0;i<10 && root;i++) {
                const lab = Array.from(root.querySelectorAll('label,button,span,div')).find(e => (e.innerText||'').trim() === '国内');
                if (lab) { lab.click(); return true; }
                root = root.parentElement;
              }
              return false;
            }"""
        )
        page.wait_for_timeout(1000)
        area2 = page.evaluate(
            """() => {
              const heads = Array.from(document.querySelectorAll('h2,h3,h4,div,span,label'));
              let anchor = null;
              for (const el of heads) {
                const t = (el.innerText||'').trim().split('\\n')[0].trim().replace(/必須/g,'').trim();
                if (t === '発送地') { anchor = el; break; }
              }
              let root = anchor && anchor.parentElement;
              for (let i=0;i<10 && root;i++) {
                if ((root.innerText||'').includes('国内') && (root.innerText||'').includes('海外')) {
                  return {
                    text: (root.innerText||'').slice(0,600),
                    selects: Array.from(root.querySelectorAll('.Select, select, input')).map(e => ({
                      tag: e.tagName, type: e.type||'', class:(e.className||'').toString().slice(0,80),
                      placeholder: e.placeholder||'', value: e.value||'',
                      text: (e.innerText||'').trim().slice(0,40)
                    }))
                  };
                }
                root = root.parentElement;
              }
              return null;
            }"""
        )
        print("=== AREA AFTER 国内 ===")
        print(json.dumps(area2, ensure_ascii=False, indent=2)[:4000])

        # Shipping: if methods already listed, try selecting checkbox/row
        ship = page.evaluate(
            """() => {
              const i = (document.body.innerText||'').indexOf('配送方法');
              const chunk = (document.body.innerText||'').slice(i, i+800);
              const rows = Array.from(document.querySelectorAll('tr, [class*=shipping], [class*=Shipping]')).map(e => ({
                class: (e.className||'').toString().slice(0,80),
                text: (e.innerText||'').trim().replace(/\\s+/g,' ').slice(0,100),
                checked: !!(e.querySelector('input:checked'))
              })).filter(r => /ヤマト|配送|送料/.test(r.text)).slice(0,20);
              return {chunk, rows};
            }"""
        )
        print("=== SHIP ROWS ===")
        print(json.dumps(ship, ensure_ascii=False, indent=2)[:4000])

        page.screenshot(path=str(s.workspace_dir / "buyma" / "probe_selects.png"), full_page=True)
        (s.workspace_dir / "buyma" / "probe_selects.json").write_text(
            json.dumps({"selects": selects, "size_ui": size_ui, "color_opts": color_opts, "area": area, "area2": area2, "ship": ship}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
