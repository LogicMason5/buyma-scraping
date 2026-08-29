"""Focus probe: size Select options + area toggle radios + shipping row selection."""

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
        page.goto(s.buyma_new_listing_url + "&_fresh=probe4", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        session._dismiss_onboarding()
        session._select_category("レディースファッション ジャケット・アウター ジャケット")
        page.wait_for_timeout(1500)

        # Click サイズ tab carefully (within color/size area)
        page.evaluate(
            """() => {
              const heads = Array.from(document.querySelectorAll('*'));
              for (const el of heads) {
                if ((el.childNodes.length === 1) && (el.textContent||'').trim() === 'サイズ' && el.children.length === 0) {
                  el.click(); return 'clicked-text-node-parent:'+el.parentElement.tagName;
                }
              }
              for (const el of document.querySelectorAll('button,a,li,div,span')) {
                if ((el.innerText||'').trim() === 'サイズ' && (el.innerText||'').trim().length === 3) {
                  el.click(); return 'clicked:'+el.tagName+':'+(el.className||'').toString().slice(0,40);
                }
              }
              return 'fail';
            }"""
        )
        page.wait_for_timeout(800)

        # Find Select with 選択してください near 色・サイズ
        open_size = page.evaluate(
            """() => {
              const heads = Array.from(document.querySelectorAll('h2,h3,h4,div,span,label'));
              let anchor = null;
              for (const el of heads) {
                const t = (el.innerText||'').trim().split('\\n')[0].trim().replace(/必須/g,'').trim();
                if (t === '色・サイズ') { anchor = el; break; }
              }
              let root = anchor && anchor.parentElement;
              for (let i=0;i<12 && root;i++) {
                const selects = Array.from(root.querySelectorAll('.Select'));
                if (selects.length) {
                  const target = selects.find(s => (s.innerText||'').includes('選択してください')) || selects[0];
                  target.querySelector('.Select-control')?.click();
                  return {ok:true, count: selects.length, text: (target.innerText||'').slice(0,80), class: (target.className||'').toString()};
                }
                root = root.parentElement;
              }
              // fallback: any Select with 選択してください below color/size y
              for (const s of document.querySelectorAll('.Select')) {
                if ((s.innerText||'').includes('選択してください')) {
                  s.querySelector('.Select-control')?.click();
                  return {ok:true, fallback:true, class:(s.className||'').toString()};
                }
              }
              return {ok:false};
            }"""
        )
        print("open size select:", open_size)
        page.wait_for_timeout(800)
        size_opts = page.evaluate(
            """() => {
              const opts = Array.from(document.querySelectorAll('.Select-option, [class*=Select-option], [role=option]')).map(e => (e.innerText||'').trim());
              const menu = document.querySelector('.Select-menu, .Select-menu-outer');
              return {
                opts,
                menuText: menu ? (menu.innerText||'').slice(0,800) : null,
                openSelects: Array.from(document.querySelectorAll('.Select.is-open, .Select.is-focused')).map(e => (e.className||'').toString().slice(0,100))
              };
            }"""
        )
        print("size opts:", json.dumps(size_opts, ensure_ascii=False, indent=2)[:3000])

        # Pick first reasonable size
        if size_opts.get("opts"):
            pick = None
            for cand in ("フリー", "F", "ONESIZE", "ONE SIZE", "ユニセックス", "S", "M"):
                if cand in size_opts["opts"]:
                    pick = cand
                    break
            if not pick:
                pick = size_opts["opts"][0]
            page.evaluate(
                """(pick) => {
                  const opt = Array.from(document.querySelectorAll('.Select-option, [class*=Select-option], [role=option]')).find(e => (e.innerText||'').trim() === pick);
                  if (opt) { opt.click(); return true; }
                  return false;
                }""",
                pick,
            )
            print("picked size:", pick)
            page.wait_for_timeout(600)

        # Color tab
        page.evaluate(
            """() => {
              for (const el of document.querySelectorAll('button,a,li,div,span')) {
                if ((el.innerText||'').trim() === '色' && (el.innerText||'').length <= 2) { el.click(); return true; }
              }
              return false;
            }"""
        )
        page.wait_for_timeout(600)
        color_state = page.evaluate(
            """() => {
              const input = document.querySelector("input[placeholder='色指定なし']");
              const texts = (document.body.innerText||'').split('\\n').map(t=>t.trim()).filter(Boolean);
              const i = texts.findIndex(t => t === '色・サイズ');
              return {hasColorInput: !!input, slice: texts.slice(i, i+25)};
            }"""
        )
        print("color state:", json.dumps(color_state, ensure_ascii=False, indent=2))
        if color_state.get("hasColorInput"):
            page.locator("input[placeholder='色指定なし']").click()
            page.wait_for_timeout(600)
            page.keyboard.type("その他")
            page.wait_for_timeout(500)
            color_opts = page.evaluate(
                """() => Array.from(document.querySelectorAll('.Select-option, [role=option], li')).map(e => (e.innerText||'').trim()).filter(Boolean).slice(0,30)"""
            )
            print("color opts after type:", color_opts)
            page.evaluate(
                """() => {
                  const opt = Array.from(document.querySelectorAll('.Select-option, [role=option], li, div, button')).find(e => {
                    const t = (e.innerText||'').trim();
                    return t === 'その他' || t.includes('その他');
                  });
                  if (opt) { opt.click(); return true; }
                  return false;
                }"""
            )
            page.wait_for_timeout(500)

        # Areas: inspect radio names for 買付地/発送地
        radios = page.evaluate(
            """() => Array.from(document.querySelectorAll('input[type=radio]')).map(e => ({
              name: e.name, value: e.value, checked: e.checked, id: e.id,
              label: ((e.closest('label')||{}).innerText||'').trim().slice(0,30),
              parentText: ((e.parentElement||{}).innerText||'').trim().slice(0,40)
            }))"""
        )
        print("radios:", json.dumps(radios, ensure_ascii=False, indent=2)[:4000])

        # Click 国内 for 発送地 via nearby structure using get_by_text scoped
        # Try all labels named 国内
        n = page.locator("label:has-text('国内')").count()
        print("国内 labels:", n)
        for i in range(n):
            try:
                page.locator("label:has-text('国内')").nth(i).click(timeout=1500)
                page.wait_for_timeout(700)
            except Exception as exc:
                print("label click fail", i, exc)
        prefs = page.evaluate(
            """() => {
              const texts = (document.body.innerText||'').split('\\n').map(t=>t.trim()).filter(Boolean);
              const i = texts.findIndex(t => t === '発送地');
              const j = texts.findIndex(t => t === '買付地');
              return {ship: texts.slice(i, i+25), buy: texts.slice(j, j+25),
                selects: Array.from(document.querySelectorAll('.Select')).map(e => (e.innerText||'').trim().split('\\n')[0]).slice(0,20)};
            }"""
        )
        print("after 国内 labels:", json.dumps(prefs, ensure_ascii=False, indent=2)[:3000])

        # Open any Select that still says 選択してください and look for 愛知県
        page.evaluate(
            """() => {
              for (const s of document.querySelectorAll('.Select')) {
                const t = (s.innerText||'').trim();
                if (t.includes('選択してください') || t.includes('都道府県') || t.includes('地域')) {
                  s.querySelector('.Select-control')?.click();
                  return t.slice(0,80);
                }
              }
              // also try selects near 発送地
              return null;
            }"""
        )
        page.wait_for_timeout(600)
        area_opts = page.evaluate(
            """() => {
              const menu = document.querySelector('.Select-menu, .Select-menu-outer');
              const opts = Array.from(document.querySelectorAll('.Select-option')).map(e => (e.innerText||'').trim());
              return {opts: opts.slice(0,60), menu: menu ? menu.innerText.slice(0,500) : null};
            }"""
        )
        print("area opts:", json.dumps(area_opts, ensure_ascii=False, indent=2)[:3000])

        # Shipping methods already listed — are they checkboxes?
        ship_html = page.evaluate(
            """() => {
              const heads = Array.from(document.querySelectorAll('h2,h3,h4,div,span'));
              let anchor = null;
              for (const el of heads) {
                const t = (el.innerText||'').trim().split('\\n')[0].trim().replace(/必須/g,'').trim();
                if (t === '配送方法') { anchor = el; break; }
              }
              let root = anchor && anchor.parentElement;
              for (let i=0;i<12 && root;i++) {
                if ((root.innerText||'').includes('配送方法を追加')) {
                  return {
                    html: root.innerHTML.slice(0,4000),
                    text: (root.innerText||'').slice(0,800),
                    checks: Array.from(root.querySelectorAll('input')).map(e => ({type:e.type,name:e.name,value:e.value,checked:e.checked,class:(e.className||'').toString().slice(0,60)}))
                  };
                }
                root = root.parentElement;
              }
              return null;
            }"""
        )
        print("ship section checks:", json.dumps((ship_html or {}).get("checks"), ensure_ascii=False, indent=2))
        print("ship text:", (ship_html or {}).get("text", "")[:500])
        # save html snippet
        (s.workspace_dir / "buyma" / "ship_section.html").write_text((ship_html or {}).get("html") or "", encoding="utf-8")

        page.screenshot(path=str(s.workspace_dir / "buyma" / "probe_focus.png"), full_page=True)
        (s.workspace_dir / "buyma" / "probe_focus.json").write_text(
            json.dumps(
                {
                    "size_opts": size_opts,
                    "color_state": color_state,
                    "radios": radios,
                    "prefs": prefs,
                    "area_opts": area_opts,
                    "ship_text": (ship_html or {}).get("text"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
