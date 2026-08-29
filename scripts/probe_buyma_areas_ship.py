"""Finish area cascade + shipping selection after color/size ok."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.buyma.buyma_browser_service import BuymaBrowserSession
from core.config import clear_settings_cache, get_settings


def click_radio_label(page, name: str, value: str) -> None:
    page.locator(f"label.bmm-c-radio:has(input[name='{name}'][value='{value}'])").click()
    page.wait_for_timeout(700)


def open_select_in_section(page, section_title: str, index: int = 0) -> int:
    return page.evaluate(
        """({ section_title, index }) => {
          const heads = Array.from(document.querySelectorAll('.bmm-c-summary__ttl'));
          for (const h of heads) {
            if ((h.innerText||'').trim() !== section_title) continue;
            const root = h.closest('.bmm-c-panel__item');
            if (!root) return -1;
            const controls = Array.from(root.querySelectorAll('.Select .Select-control'));
            if (!controls[index]) return controls.length;
            controls[index].click();
            return controls.length;
          }
          return -1;
        }""",
        {"section_title": section_title, "index": index},
    )


def pick_option(page, label: str) -> bool:
    opts = page.locator(".Select-option")
    n = opts.count()
    for i in range(n):
        t = opts.nth(i).inner_text().strip()
        if t == label or label in t:
            opts.nth(i).click()
            page.wait_for_timeout(600)
            return True
    return False


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
        page.goto(s.buyma_new_listing_url + "&_fresh=probe8", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        session._dismiss_onboarding()
        session._select_category("レディースファッション ジャケット・アウター ジャケット")
        page.wait_for_timeout(800)

        # color + size
        page.locator(".sell-color-option").first.click()
        page.wait_for_timeout(300)
        page.locator(".sell-color-option__name", has_text="ブラック（黒）系").click()
        page.wait_for_timeout(500)
        page.locator(".sell-variation__tab-item", has_text="サイズ").click()
        page.wait_for_timeout(400)
        page.locator(".sell-variation .Select .Select-control").first.click()
        page.wait_for_timeout(400)
        pick_option(page, "バリエーションなし")
        print("variation:", page.locator(".sell-variation").inner_text()[:400])

        # buy area overseas -> Europe -> Italy
        click_radio_label(page, "purchaseArea-region", "overseas")
        n = open_select_in_section(page, "買付地", 0)
        print("buy selects", n)
        page.wait_for_timeout(400)
        opts = page.evaluate("() => Array.from(document.querySelectorAll('.Select-option')).map(e => e.innerText.trim())")
        print("buy L1", opts[:30])
        # try ヨーロッパ
        if not pick_option(page, "ヨーロッパ"):
            pick_option(page, opts[0] if opts else "")
        n = open_select_in_section(page, "買付地", 1)
        page.wait_for_timeout(400)
        opts = page.evaluate("() => Array.from(document.querySelectorAll('.Select-option')).map(e => e.innerText.trim())")
        print("buy L2", opts[:30])
        pick_option(page, "イタリア")

        # ship from domestic -> Aichi
        click_radio_label(page, "shippingArea-region", "domestic")
        n = open_select_in_section(page, "発送地", 0)
        print("ship selects", n)
        page.wait_for_timeout(400)
        opts = page.evaluate("() => Array.from(document.querySelectorAll('.Select-option')).map(e => e.innerText.trim())")
        print("ship L1", opts[:40])
        pick_option(page, "愛知県")

        areas_txt = page.evaluate(
            """() => {
              const t = (document.body.innerText||'').split('\\n').map(x=>x.trim()).filter(Boolean);
              const i = t.findIndex(x => x === '買付地');
              const j = t.findIndex(x => x === '発送地');
              return {buy: t.slice(i, i+20), ship: t.slice(j, j+20)};
            }"""
        )
        print("areas:", json.dumps(areas_txt, ensure_ascii=False, indent=2))

        # shipping methods section — dump after attempting to add
        ship_html = page.evaluate(
            """() => {
              const heads = Array.from(document.querySelectorAll('.bmm-c-summary__ttl'));
              for (const h of heads) {
                if ((h.innerText||'').trim() !== '配送方法') continue;
                let root = h.closest('.bmm-c-panel') || h.parentElement;
                for (let i=0;i<10 && root;i++) {
                  if ((root.innerText||'').includes('配送方法を追加')) {
                    return {
                      text: root.innerText.slice(0,1000),
                      html: root.innerHTML.slice(0,6000),
                      inputs: Array.from(root.querySelectorAll('input')).map(e => ({type:e.type,name:e.name,value:e.value,checked:e.checked,class:(e.className||'').toString().slice(0,60)}))
                    };
                  }
                  root = root.parentElement;
                }
              }
              return null;
            }"""
        )
        print("ship inputs", (ship_html or {}).get("inputs"))
        print("ship text", ((ship_html or {}).get("text") or "")[:700])
        (s.workspace_dir / "buyma" / "ship_final.html").write_text((ship_html or {}).get("html") or "", encoding="utf-8")

        # Try adding shipping properly
        page.get_by_text("配送方法を追加", exact=True).click()
        page.wait_for_timeout(700)
        page.get_by_text("選択してください", exact=True).last.click()
        page.wait_for_timeout(400)
        page.get_by_role("option", name="ヤマト運輸 - 宅急便", exact=True).click()
        page.wait_for_timeout(600)
        fee = page.locator("#modal-root input.bmm-c-text-field--half-size-char, input.bmm-c-text-field--half-size-char.bmm-c-text-field--size-free").last
        fee.click()
        fee.fill("1200")
        page.locator("input[name='shipping_date_from']").fill("7")
        page.locator("input[name='shipping_date_to']").fill("14")
        page.locator("label.bmm-c-radio:has(input[name='billType'][value='sender'])").click()
        page.locator("label.bmm-c-radio:has(input[name='withTracking'][value='yes'])").click()
        page.locator("label.bmm-c-radio:has(input[name='period_type'][value='after-order'])").click()
        vals = page.evaluate(
            """() => ({
              fee: (document.querySelector('#modal-root input.bmm-c-text-field--half-size-char')||{}).value,
              from: (document.querySelector("input[name='shipping_date_from']")||{}).value,
              to: (document.querySelector("input[name='shipping_date_to']")||{}).value,
            })"""
        )
        print("modal vals", vals)
        page.get_by_role("button", name="設定する", exact=True).click()
        page.wait_for_timeout(1200)
        after = page.evaluate(
            """() => ({
              modal: !!document.querySelector('#modal-root .bmm-c-modal-overlay'),
              modalText: ((document.querySelector('#modal-root')||{}).innerText||'').slice(0,400),
              shipText: (() => {
                const t=(document.body.innerText||'');
                const i=t.indexOf('配送方法');
                return t.slice(i, i+500);
              })()
            })"""
        )
        print("after save", json.dumps(after, ensure_ascii=False, indent=2))
        page.screenshot(path=str(s.workspace_dir / "buyma" / "probe_areas_ship.png"), full_page=True)
    finally:
        session.close()


if __name__ == "__main__":
    main()
