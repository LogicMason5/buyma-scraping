"""Probe color/size, shipping, and ship-from sections on Buyma new listing."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.buyma.buyma_browser_service import BuymaBrowserSession
from core.config import clear_settings_cache, get_settings


def dump_section(page, label: str) -> dict:
    return page.evaluate(
        """(label) => {
          const heads = Array.from(document.querySelectorAll('h2,h3,h4,label,div,span,th,dt,legend'));
          let root = null;
          for (const el of heads) {
            const t = (el.innerText || '').trim().split('\\n')[0].trim().replace(/必須/g,'').trim();
            if (t !== label && !t.startsWith(label)) continue;
            if (t.length > label.length + 20) continue;
            root = el.parentElement;
            for (let i=0;i<8 && root;i++) {
              if ((root.innerText||'').length > 40) break;
              root = root.parentElement;
            }
            break;
          }
          if (!root) root = document.body;
          const text = (root.innerText || '').split('\\n').map(t => t.trim()).filter(Boolean).slice(0, 80);
          const inputs = Array.from(root.querySelectorAll('input,textarea,select,button,[role=option],[role=checkbox]')).slice(0, 60).map(e => ({
            tag: e.tagName, type: e.type||'', name: e.name||'', role: e.getAttribute('role')||'',
            placeholder: e.placeholder||'', value: e.value||'',
            checked: !!e.checked, text: (e.innerText||e.textContent||'').trim().slice(0,40),
            class: (e.className||'').toString().slice(0,70),
            aria: e.getAttribute('aria-label')||''
          }));
          const checks = Array.from(document.querySelectorAll('input[type=checkbox], [role=checkbox]')).slice(0,40).map(e => ({
            name: e.name, value: e.value, checked: !!e.checked,
            label: ((e.closest('label')||e.parentElement||{}).innerText||'').trim().slice(0,50),
            class: (e.className||'').toString().slice(0,60)
          }));
          return {label, text, inputs, checksSample: checks};
        }""",
        label,
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
        page.goto(s.buyma_new_listing_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        session._dismiss_onboarding()

        # Category so sizes appear
        session._select_category("レディースファッション ジャケット・アウター ジャケット")
        page.wait_for_timeout(1000)

        color_size = dump_section(page, "色・サイズ")
        # Click size tab area and dump again
        try:
            page.get_by_text("サイズ", exact=True).first.click(timeout=2000)
            page.wait_for_timeout(600)
        except Exception as exc:  # noqa: BLE001
            print("size tab click fail", exc)
        size_after = dump_section(page, "色・サイズ")

        # Try open color placeholder
        try:
            page.locator("input[placeholder='色指定なし'], text=色指定なし, text=選択してください").first.click(timeout=2000)
            page.wait_for_timeout(600)
        except Exception as exc:  # noqa: BLE001
            print("color open fail", exc)
        color_open = page.evaluate(
            """() => {
              const texts = (document.body.innerText||'').split('\\n').map(t=>t.trim()).filter(Boolean);
              const idx = texts.findIndex(t => t.includes('色') || t.includes('系統'));
              return {
                nearby: texts.slice(Math.max(0,idx-5), idx+40),
                options: Array.from(document.querySelectorAll('[role=option], li, .bmm-c-select-option, button')).slice(0,80).map(e => (e.innerText||'').trim().slice(0,40)).filter(Boolean)
              };
            }"""
        )

        ship_from = dump_section(page, "発送地")
        buy_area = dump_section(page, "買付地")

        # Open shipping modal
        page.get_by_text("配送方法を追加", exact=True).click()
        page.wait_for_timeout(800)
        try:
            page.get_by_text("選択してください", exact=True).last.click()
            page.wait_for_timeout(400)
            page.get_by_role("option", name="ヤマト運輸 - 宅急便", exact=True).click()
            page.wait_for_timeout(800)
        except Exception as exc:  # noqa: BLE001
            print("ship select fail", exc)
        ship_modal = page.evaluate(
            """() => {
              const root = document.querySelector('#modal-root') || document.body;
              return {
                texts: (root.innerText||'').split('\\n').map(t=>t.trim()).filter(Boolean).slice(0,100),
                inputs: Array.from(root.querySelectorAll('input,textarea,select,button')).map(e => ({
                  tag: e.tagName, type: e.type||'', name: e.name||'', id: e.id||'',
                  placeholder: e.placeholder||'', value: e.value||'',
                  text: (e.innerText||'').trim().slice(0,40),
                  class: (e.className||'').toString().slice(0,80),
                  disabled: !!e.disabled
                }))
              };
            }"""
        )

        out = {
            "color_size": color_size,
            "size_after": size_after,
            "color_open": color_open,
            "ship_from": ship_from,
            "buy_area": buy_area,
            "ship_modal": ship_modal,
        }
        path = s.workspace_dir / "buyma" / "remaining_probe.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("wrote", path)
        print("=== size texts ===")
        print("\n".join((size_after.get("text") or [])[:40]))
        print("=== size checks ===")
        print(json.dumps(size_after.get("checksSample") or [], ensure_ascii=False, indent=2)[:2500])
        print("=== color options sample ===")
        print(json.dumps((color_open.get("options") or [])[:40], ensure_ascii=False, indent=2))
        print("=== ship_from texts ===")
        print("\n".join((ship_from.get("text") or [])[:40]))
        print("=== ship_modal texts ===")
        print("\n".join((ship_modal.get("texts") or [])[:50]))
        print("=== ship_modal inputs ===")
        print(json.dumps(ship_modal.get("inputs") or [], ensure_ascii=False, indent=2)[:3500])
        page.screenshot(path=str(s.workspace_dir / "buyma" / "remaining_probe.png"), full_page=True)
    finally:
        session.close()


if __name__ == "__main__":
    main()
