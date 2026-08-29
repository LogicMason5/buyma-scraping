"""Interactive probe: complete category path then dump size/shipping widgets."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.buyma.buyma_browser_service import BuymaBrowserSession
from core.config import clear_settings_cache, get_settings


def visible_short_texts(page, limit=120):
    return page.evaluate(
        """(limit) => Array.from(new Set(
          Array.from(document.querySelectorAll('li,button,a,div[role=option],label,td,th,span'))
            .filter(e => e.offsetParent && (e.innerText||'').trim().length && (e.innerText||'').trim().length < 40)
            .map(e => (e.innerText||'').trim().split('\\n')[0].trim())
        )).slice(0, limit)""",
        limit,
    )


def click_text(page, label: str) -> bool:
    for sel in (
        f"li:has-text('{label}')",
        f"div[role='option']:has-text('{label}')",
        f"button:has-text('{label}')",
        f"a:has-text('{label}')",
        f"label:has-text('{label}')",
        f"text={label}",
    ):
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible(timeout=600):
                loc.click(timeout=3000)
                page.wait_for_timeout(700)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


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

        log = []
        # Open first category dropdown (first 選択してください near カテゴリ)
        selects = page.locator("text=選択してください")
        count = selects.count()
        log.append(f"選択してください count={count}")
        if count:
            selects.nth(0).click()
            page.wait_for_timeout(800)
        click_text(page, "レディースファッション")
        log.append("selected レディースファッション")
        page.wait_for_timeout(800)
        # second dropdown
        selects = page.locator("text=選択してください")
        log.append(f"after L1 選択してください count={selects.count()}")
        if selects.count():
            selects.nth(0).click()
            page.wait_for_timeout(800)
        log.append({"after_l1_open": visible_short_texts(page)})
        for label in ("ジャケット・アウター", "コート", "トップス"):
            if click_text(page, label):
                log.append(f"selected L2 {label}")
                break
        page.wait_for_timeout(800)
        selects = page.locator("text=選択してください")
        log.append(f"after L2 選択してください count={selects.count()}")
        if selects.count():
            selects.nth(0).click()
            page.wait_for_timeout(800)
        log.append({"after_l2_open": visible_short_texts(page)})
        for label in ("ジャケット", "ブルゾン", "コート", "テーラードジャケット"):
            if click_text(page, label):
                log.append(f"selected L3 {label}")
                break
        page.wait_for_timeout(1200)

        # Size tab after category
        click_text(page, "サイズ")
        page.wait_for_timeout(1000)
        size_dump = page.evaluate(
            """() => {
              // Find size panel by tab selected / headers
              const headers = Array.from(document.querySelectorAll('th,td,button,label,div'))
                .filter(e => e.offsetParent)
                .map(e => (e.innerText||'').trim().split('\\n')[0])
                .filter(t => t && t.length < 25);
              const uniq = Array.from(new Set(headers));
              const sizeish = uniq.filter(t => /S|M|L|XL|フリー|サイズ|ONE|UNI|38|36|40|42/.test(t));
              const checks = Array.from(document.querySelectorAll('input[type=checkbox]')).map(e => ({
                checked: e.checked,
                text: (e.closest('label')||e.parentElement||{}).innerText||'',
                name: e.name,
              })).filter(x => x.text && x.text.length < 40).slice(0,40);
              return {sizeish: sizeish.slice(0,60), checks};
            }"""
        )
        log.append({"size_dump": size_dump})

        # Shipping modal fields
        click_text(page, "配送方法を追加")
        page.wait_for_timeout(1000)
        ship_selects = page.locator("text=選択してください")
        log.append(f"shipping 選択してください count={ship_selects.count()}")
        # click the one inside modal if possible
        modal = page.locator("text=配送方法追加").first
        if modal.count():
            log.append("modal visible")
        if ship_selects.count():
            ship_selects.nth(ship_selects.count() - 1).click()
            page.wait_for_timeout(800)
        log.append({"shipping_options": visible_short_texts(page, 80)})

        path = settings.workspace_dir / "buyma" / "listing_interactive_probe.json"
        path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(log, ensure_ascii=False, indent=2)[:6000])
        print("wrote", path)
        page.screenshot(path=str(settings.workspace_dir / "buyma" / "listing_interactive.png"), full_page=False)
        page.wait_for_timeout(2000)
    finally:
        session.close()


if __name__ == "__main__":
    main()
