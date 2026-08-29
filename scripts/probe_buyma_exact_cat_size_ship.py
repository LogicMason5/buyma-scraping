"""Exact category ジャケット + inspect size/color widgets + complete shipping modal."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.buyma.buyma_browser_service import BuymaBrowserSession
from core.config import clear_settings_cache, get_settings


def click_exact(page, label: str) -> bool:
    return bool(
        page.evaluate(
            """(label) => {
              const nodes = Array.from(document.querySelectorAll('li,div[role=option],button,a,span,label'));
              for (const el of nodes) {
                if (!el.offsetParent) continue;
                const t = (el.innerText || '').trim().split('\\n')[0].trim();
                if (t === label) { el.click(); return true; }
              }
              return false;
            }""",
            label,
        )
    )


def open_first_select(page) -> None:
    # Prefer visible dropdown that still says 選択してください near category
    page.evaluate(
        """() => {
          const nodes = Array.from(document.querySelectorAll('button,div,span,a'));
          for (const el of nodes) {
            if (!el.offsetParent) continue;
            const t = (el.innerText || '').trim().split('\\n')[0].trim();
            if (t === '選択してください') { el.click(); return true; }
          }
          return false;
        }"""
    )
    page.wait_for_timeout(700)


def main() -> None:
    clear_settings_cache()
    settings = get_settings()
    session = BuymaBrowserSession()
    session.start()
    try:
        assert session.page is not None
        page = session.page
        if not session.ensure_logged_in(timeout_seconds=45):
            raise SystemExit("not logged in")
        page.goto(settings.buyma_new_listing_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        session._dismiss_onboarding()

        open_first_select(page)
        print("L1", click_exact(page, "レディースファッション"))
        page.wait_for_timeout(600)
        open_first_select(page)
        print("L2", click_exact(page, "アウター"))
        page.wait_for_timeout(600)
        open_first_select(page)
        print("L3", click_exact(page, "ジャケット"))
        page.wait_for_timeout(1500)
        cat = page.evaluate(
            """() => {
              const t = Array.from(document.querySelectorAll('div,span,button'))
                .map(e => (e.innerText||'').trim())
                .filter(t => t.includes('レディース') || t.includes('アウター') || t.includes('ジャケット'))
                .slice(0,20);
              return t;
            }"""
        )
        print("cat texts", cat[:20])

        # Click color system cell / 色指定なし area
        page.evaluate(
            """() => {
              const el = Array.from(document.querySelectorAll('input,td,div,span')).find(e =>
                e.offsetParent && ((e.placeholder||'') === '色指定なし' || (e.innerText||'').trim() === '色指定なし')
              );
              if (el) el.click();
              return !!el;
            }"""
        )
        page.wait_for_timeout(800)
        print("color options", page.evaluate(
            """() => Array.from(document.querySelectorAll('li,div[role=option],button,label'))
              .filter(e => e.offsetParent)
              .map(e => (e.innerText||'').trim().split('\\n')[0])
              .filter(t => t && t.length < 20)
              .slice(0,60)"""
        ))
        for label in ("ブラック", "黒", "ホワイト", "その他", "ブラウン", "ベージュ", "グレー"):
            if click_exact(page, label):
                print("color", label)
                break
        page.wait_for_timeout(800)

        # size tab
        click_exact(page, "サイズ")
        page.wait_for_timeout(1000)
        size_info = page.evaluate(
            """() => {
              const html = document.body.innerText;
              const has = ['XS','S','M','L','XL','フリー','サイズ表','サイズを選択'];
              return {
                has: Object.fromEntries(has.map(h => [h, html.includes(h)])),
                buttons: Array.from(document.querySelectorAll('button,label'))
                  .filter(e => e.offsetParent)
                  .map(e => (e.innerText||'').trim().split('\\n')[0])
                  .filter(t => /^(XXS|XS|S|M|L|XL|XXL|F|フリー)$/.test(t)),
                checks: Array.from(document.querySelectorAll('input[type=checkbox]'))
                  .filter(e => e.offsetParent)
                  .map(e => ((e.closest('label')||e.parentElement||{}).innerText||'').trim().slice(0,20))
                  .filter(Boolean)
                  .slice(0,40)
              };
            }"""
        )
        print("size_info", json.dumps(size_info, ensure_ascii=False, indent=2))

        # Complete shipping modal
        click_exact(page, "配送方法を追加")
        page.wait_for_timeout(800)
        # open method dropdown inside modal
        page.evaluate(
            """() => {
              const modal = Array.from(document.querySelectorAll('div,section')).find(e =>
                (e.innerText||'').includes('配送方法追加') && e.querySelectorAll('input').length > 0
              );
              if (!modal) return false;
              const sel = Array.from(modal.querySelectorAll('button,div,span')).find(e =>
                (e.innerText||'').trim().split('\\n')[0] === '選択してください' ||
                (e.innerText||'').includes('ヤマト')
              );
              if (sel && (sel.innerText||'').trim().split('\\n')[0] === '選択してください') sel.click();
              return true;
            }"""
        )
        page.wait_for_timeout(500)
        click_exact(page, "ヤマト運輸 - 宅急便")
        page.wait_for_timeout(500)
        # fill fee and days inside modal
        filled = page.evaluate(
            """({fee, d1, d2}) => {
              const modal = Array.from(document.querySelectorAll('div,section')).find(e =>
                (e.innerText||'').includes('配送方法追加') && (e.innerText||'').includes('配送料金')
              );
              if (!modal) return {ok:false};
              const inputs = Array.from(modal.querySelectorAll('input.bmm-c-text-field'));
              // fee half-size
              const feeInput = modal.querySelector('input.bmm-c-text-field--half-size-char') || inputs.find(i => (i.previousElementSibling||{}).textContent === '¥');
              const terms = Array.from(modal.querySelectorAll('input.sell-shipping-create-modal__shipping-term'));
              if (feeInput) {
                feeInput.focus();
                feeInput.value = fee;
                feeInput.dispatchEvent(new Event('input', {bubbles:true}));
                feeInput.dispatchEvent(new Event('change', {bubbles:true}));
              }
              if (terms[0]) { terms[0].value = d1; terms[0].dispatchEvent(new Event('input', {bubbles:true})); }
              if (terms[1]) { terms[1].value = d2; terms[1].dispatchEvent(new Event('input', {bubbles:true})); }
              // tracking あり
              const track = Array.from(modal.querySelectorAll('label,span,button')).find(e => (e.innerText||'').trim() === 'あり');
              if (track) track.click();
              const buttons = Array.from(modal.querySelectorAll('button')).map(b => (b.innerText||'').trim());
              return {ok:true, buttons, fee: feeInput && feeInput.value, terms: terms.map(t => t.value)};
            }""",
            {"fee": "1200", "d1": "7", "d2": "14"},
        )
        print("ship fill", filled)
        # click add/save in modal
        for label in ("追加する", "設定する", "保存する", "決定", "OK", "追加"):
            if click_exact(page, label):
                print("modal confirm", label)
                break
        page.wait_for_timeout(1000)
        page.screenshot(path=str(settings.workspace_dir / "buyma" / "listing_after_ship.png"), full_page=False)
    finally:
        session.close()


if __name__ == "__main__":
    main()
