"""Deep probe: size checkboxes + shipping modal commit + ship-from cascade."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.buyma.buyma_browser_service import BuymaBrowserSession
from core.config import clear_settings_cache, get_settings


def react_fill(page, selector: str, value: str) -> None:
    page.evaluate(
        """({ selector, value }) => {
          const el = document.querySelector(selector);
          if (!el) return false;
          el.focus();
          const proto = window.HTMLInputElement.prototype;
          const desc = Object.getOwnPropertyDescriptor(proto, 'value');
          if (desc && desc.set) desc.set.call(el, value);
          else el.value = value;
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
          return true;
        }""",
        {"selector": selector, "value": value},
    )


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
        page.goto(s.buyma_new_listing_url + "&_fresh=probe2", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        session._dismiss_onboarding()
        session._select_category("レディースファッション ジャケット・アウター ジャケット")
        page.wait_for_timeout(1200)

        # Color/size DOM focused
        cs = page.evaluate(
            """() => {
              // Find heading 色・サイズ
              const heads = Array.from(document.querySelectorAll('h2,h3,h4,div,span,label'));
              let anchor = null;
              for (const el of heads) {
                const t = (el.innerText||'').trim().split('\\n')[0].trim().replace(/必須/g,'').trim();
                if (t === '色・サイズ' || t === '色 / サイズ') { anchor = el; break; }
              }
              let section = anchor ? anchor.parentElement : null;
              for (let i=0;i<10 && section;i++) {
                if (section.querySelectorAll('input[type=checkbox]').length >= 3) break;
                section = section.parentElement;
              }
              const html = section ? section.innerHTML.slice(0, 8000) : '';
              const texts = section ? (section.innerText||'').split('\\n').map(t=>t.trim()).filter(Boolean).slice(0,100) : [];
              const checks = Array.from((section||document).querySelectorAll('input[type=checkbox]')).map(e => ({
                checked: e.checked, value: e.value, name: e.name, id: e.id,
                label: ((e.closest('label')||e.parentElement||{}).innerText||'').trim().replace(/\\s+/g,' ').slice(0,80),
                class: (e.className||'').toString().slice(0,60)
              }));
              const selects = Array.from((section||document).querySelectorAll('input[type=text], select, [role=combobox], button')).slice(0,30).map(e => ({
                tag: e.tagName, type: e.type||'', placeholder: e.placeholder||'',
                text: (e.innerText||'').trim().slice(0,40),
                class: (e.className||'').toString().slice(0,70)
              }));
              return {found: !!section, texts, checks, selects, htmlStart: html.slice(0,1500)};
            }"""
        )
        print("=== COLOR/SIZE ===")
        print("found", cs.get("found"), "checks", len(cs.get("checks") or []))
        print("texts:", json.dumps(cs.get("texts") or [], ensure_ascii=False, indent=2)[:2000])
        print("checks:", json.dumps(cs.get("checks") or [], ensure_ascii=False, indent=2)[:3000])
        print("selects:", json.dumps(cs.get("selects") or [], ensure_ascii=False, indent=2)[:2000])

        # Try clicking サイズ tab then dump again
        try:
            page.locator("text=サイズ").first.click(timeout=2000)
            page.wait_for_timeout(800)
        except Exception as exc:
            print("size click", exc)
        cs2 = page.evaluate(
            """() => {
              const texts = (document.body.innerText||'').split('\\n').map(t=>t.trim()).filter(Boolean);
              const i = texts.findIndex(t => t === '色・サイズ' || t.startsWith('色・サイズ'));
              return texts.slice(i, i+60);
            }"""
        )
        print("body near color/size:", json.dumps(cs2, ensure_ascii=False, indent=2))

        # Ship-from: click 国内 under 発送地
        print("=== SHIP FROM ===")
        before = page.evaluate("() => (document.body.innerText||'').includes('愛知県')")
        # Prefer scoped click near 発送地
        clicked = page.evaluate(
            """() => {
              const heads = Array.from(document.querySelectorAll('h2,h3,h4,div,span,label'));
              let anchor = null;
              for (const el of heads) {
                const t = (el.innerText||'').trim().split('\\n')[0].trim().replace(/必須/g,'').trim();
                if (t === '発送地') { anchor = el; break; }
              }
              if (!anchor) return 'no-anchor';
              let root = anchor.parentElement;
              for (let i=0;i<8 && root;i++) {
                const btns = Array.from(root.querySelectorAll('button, a, label, span, div'));
                const domestic = btns.find(b => ((b.innerText||'').trim() === '国内'));
                if (domestic) { domestic.click(); return 'clicked-domestic'; }
                root = root.parentElement;
              }
              return 'no-domestic';
            }"""
        )
        print("ship-from click:", clicked, "hadAichiBefore", before)
        page.wait_for_timeout(800)
        prefs = page.evaluate(
            """() => {
              const texts = (document.body.innerText||'').split('\\n').map(t=>t.trim()).filter(Boolean);
              const i = texts.findIndex(t => t === '発送地');
              return texts.slice(i, i+40);
            }"""
        )
        print("after domestic:", json.dumps(prefs, ensure_ascii=False, indent=2))

        # Shipping modal fill with react setter
        print("=== SHIPPING MODAL FILL ===")
        page.get_by_text("配送方法を追加", exact=True).click()
        page.wait_for_timeout(700)
        page.get_by_text("選択してください", exact=True).last.click()
        page.wait_for_timeout(400)
        page.get_by_role("option", name="ヤマト運輸 - 宅急便", exact=True).click()
        page.wait_for_timeout(700)
        # fill fee + dates
        react_fill(page, "input.bmm-c-text-field--half-size-char.bmm-c-text-field--size-free", "1200")
        # also try playwright fill
        fee = page.locator("input.bmm-c-text-field--half-size-char.bmm-c-text-field--size-free").last
        fee.click()
        fee.fill("1200")
        page.locator("input[name='shipping_date_from']").fill("7")
        page.locator("input[name='shipping_date_to']").fill("14")
        page.locator("input[name='billType'][value='sender']").check(force=True)
        page.locator("input[name='withTracking'][value='yes']").check(force=True)
        page.locator("input[name='period_type'][value='after-order']").check(force=True)
        vals = page.evaluate(
            """() => ({
              fee: (document.querySelector('input.bmm-c-text-field--half-size-char.bmm-c-text-field--size-free')||{}).value,
              from: (document.querySelector("input[name='shipping_date_from']")||{}).value,
              to: (document.querySelector("input[name='shipping_date_to']")||{}).value,
              bill: (document.querySelector("input[name='billType']:checked")||{}).value,
              track: (document.querySelector("input[name='withTracking']:checked")||{}).value,
              period: (document.querySelector("input[name='period_type']:checked")||{}).value,
            })"""
        )
        print("modal vals before save:", vals)
        page.get_by_role("button", name="設定する", exact=True).click()
        page.wait_for_timeout(1200)
        after_modal = page.evaluate(
            """() => ({
              modalVisible: !!(document.querySelector('#modal-root .bmm-c-modal-overlay')),
              modalText: ((document.querySelector('#modal-root')||{}).innerText||'').slice(0,500),
              bodyHasYamatoFee: (document.body.innerText||'').includes('1,200') || (document.body.innerText||'').includes('1200'),
              errors: Array.from(document.querySelectorAll('.bmm-c-text--error, [class*=error]')).map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,15)
            })"""
        )
        print("after 設定する:", json.dumps(after_modal, ensure_ascii=False, indent=2))
        page.screenshot(path=str(s.workspace_dir / "buyma" / "probe_deep.png"), full_page=True)

        out = {"color_size": cs, "near": cs2, "ship_from": prefs, "modal_vals": vals, "after_modal": after_modal}
        (s.workspace_dir / "buyma" / "probe_deep.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
