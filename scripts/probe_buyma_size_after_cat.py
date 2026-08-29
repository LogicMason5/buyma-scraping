"""Probe size UI after full category レディース→アウター→ジャケット, and shipping form fields."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.buyma.buyma_browser_service import BuymaBrowserSession
from core.config import clear_settings_cache, get_settings


def click_text(page, label: str) -> bool:
    for sel in (
        f"li:has-text('{label}')",
        f"div[role='option']:has-text('{label}')",
        f"button:has-text('{label}')",
        f"text={label}",
    ):
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible(timeout=700):
                loc.click(timeout=3000)
                page.wait_for_timeout(700)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def open_nth_select(page, n: int = 0) -> None:
    sels = page.locator("text=選択してください")
    if sels.count() > n:
        sels.nth(n).click()
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

        open_nth_select(page, 0)
        click_text(page, "レディースファッション")
        open_nth_select(page, 0)
        clicked = click_text(page, "アウター")
        print("L2 アウター", clicked)
        open_nth_select(page, 0)
        opts = page.evaluate(
            """() => Array.from(document.querySelectorAll('li,div[role=option],button,a'))
              .filter(e => e.offsetParent)
              .map(e => (e.innerText||'').trim().split('\\n')[0])
              .filter(t => t && t.length < 30)
              .slice(0,80)"""
        )
        print("L3 options sample", [o for o in opts if any(k in o for k in ("ジャケット", "コート", "ブルゾン", "ダウン", "パーカー"))][:30])
        for label in ("ジャケット", "テーラードジャケット", "ライダースジャケット", "ブルゾン", "コート"):
            if click_text(page, label):
                print("L3 selected", label)
                break
        page.wait_for_timeout(1500)

        # Color first - 色の系統
        click_text(page, "色")
        page.wait_for_timeout(500)
        color_ui = page.evaluate(
            """() => {
              const rows = Array.from(document.querySelectorAll('tr, [role=row]')).slice(0,20).map(r => (r.innerText||'').trim().slice(0,120));
              const inputs = Array.from(document.querySelectorAll('input')).filter(e => e.offsetParent).map(e => ({
                type:e.type, placeholder:e.placeholder, value:e.value,
                text:(e.closest('label')||e.parentElement||{}).innerText||''.toString().slice(0,40)
              })).slice(0,40);
              return {rows, inputs};
            }"""
        )
        print("color_ui rows", color_ui["rows"][:10])

        # Size tab
        click_text(page, "サイズ")
        page.wait_for_timeout(1000)
        size_ui = page.evaluate(
            """() => {
              const texts = Array.from(document.querySelectorAll('button,label,td,th,div,span,li'))
                .filter(e => e.offsetParent)
                .map(e => (e.innerText||'').trim().split('\\n')[0])
                .filter(t => t && t.length < 20);
              const uniq = Array.from(new Set(texts));
              const sizeish = uniq.filter(t => /^(XXS|XS|S|M|L|XL|XXL|F|フリー|UNI|One|ONESIZE|[0-9]{2})$/i.test(t) || t.includes('サイズ'));
              const checks = Array.from(document.querySelectorAll('input[type=checkbox],input[type=radio]'))
                .filter(e => e.offsetParent)
                .map(e => ({type:e.type, checked:e.checked, text:((e.closest('label')||e.parentElement||{}).innerText||'').trim().slice(0,30)}))
                .filter(x => x.text)
                .slice(0,50);
              return {sizeish, checks, uniqSample: uniq.slice(0,80)};
            }"""
        )
        print(json.dumps(size_ui, ensure_ascii=False, indent=2)[:3500])

        # Shipping fill
        click_text(page, "配送方法を追加")
        page.wait_for_timeout(800)
        # last 選択してください in modal
        sels = page.locator("text=選択してください")
        print("select count", sels.count())
        if sels.count():
            sels.nth(sels.count() - 1).click()
            page.wait_for_timeout(600)
        click_text(page, "ヤマト運輸 - 宅急便")
        page.wait_for_timeout(800)
        ship_fields = page.evaluate(
            """() => {
              // fields in dialog-ish area
              const inputs = Array.from(document.querySelectorAll('input,textarea')).filter(e => e.offsetParent).map(e => ({
                type:e.type, placeholder:e.placeholder, class:e.className.toString().slice(0,60), value:e.value,
                nearby:((e.previousElementSibling||e.parentElement||{}).innerText||'').toString().slice(0,40)
              }));
              return inputs.slice(0,40);
            }"""
        )
        print("ship fields", json.dumps(ship_fields, ensure_ascii=False, indent=2)[:2500])
        page.screenshot(path=str(settings.workspace_dir / "buyma" / "listing_size_after_cat.png"), full_page=False)
        path = settings.workspace_dir / "buyma" / "listing_size_after_cat.json"
        path.write_text(json.dumps({"color_ui": color_ui, "size_ui": size_ui, "ship_fields": ship_fields}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("wrote", path)
    finally:
        session.close()


if __name__ == "__main__":
    main()
