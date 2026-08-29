"""Diagnose whether Buyma React fields accept fills on sell/new?tab=b."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.buyma.buyma_browser_service import BuymaBrowserSession
from core.config import clear_settings_cache, get_settings


def main() -> None:
    clear_settings_cache()
    settings = get_settings()
    session = BuymaBrowserSession()
    session.start()
    try:
        assert session.page is not None
        if not session.ensure_logged_in(timeout_seconds=45):
            raise SystemExit("not logged in")
        page = session.page
        page.goto("https://www.buyma.com/my/sell/new?tab=b", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        session._dismiss_onboarding()

        frames = [f.url for f in page.frames]
        print("frames:", len(frames), frames[:5])

        # Precise product-name input: first text field after heading 商品名
        handle = page.evaluate_handle(
            """() => {
              const heads = Array.from(document.querySelectorAll('h2,h3,h4,label,div,span'));
              for (const el of heads) {
                const t = (el.innerText || '').trim().split('\\n')[0].trim();
                if (t !== '商品名' && t !== '商品名必須' && !/^商品名/.test(t)) continue;
                if (t.length > 20) continue;
                let root = el.parentElement;
                for (let i = 0; i < 8 && root; i++) {
                  const input = root.querySelector("input.bmm-c-text-field[type='text'], input[type='text']");
                  if (input && input.offsetParent) return input;
                  root = root.parentElement;
                }
              }
              return null;
            }"""
        )
        el = handle.as_element()
        if not el:
            print("FAIL: product name input not found")
            return
        before = el.input_value()
        print("before:", before[:80] if before else before)
        el.click()
        el.fill("【A.p.c.】Gaelle blouson TEST")
        page.wait_for_timeout(500)
        after = el.input_value()
        print("after:", after[:80] if after else after)

        # Brand
        brand = page.locator("input[placeholder*='ブランド']").first
        brand.click()
        brand.fill("A.P.C.")
        page.wait_for_timeout(1000)
        print("brand value:", brand.input_value())
        # click suggestion
        for sel in ("text=A.P.C.", "text=A.p.c.", "li:has-text('A.P.C')", "div:has-text('A.P.C')"):
            loc = page.locator(sel).first
            try:
                if loc.count() and loc.is_visible(timeout=400):
                    loc.click()
                    print("clicked brand suggestion via", sel)
                    break
            except Exception as exc:  # noqa: BLE001
                print("brand click fail", sel, exc)

        shot = settings.workspace_dir / "buyma" / "listing_fill_diag.png"
        page.screenshot(path=str(shot), full_page=False)
        print("shot", shot)
        page.wait_for_timeout(3000)
    finally:
        session.close()


if __name__ == "__main__":
    main()
