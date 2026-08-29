"""Dump shipping modal required fields after selecting Yamato."""

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
        page.goto(s.buyma_new_listing_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        session._dismiss_onboarding()
        page.get_by_text("配送方法を追加", exact=True).click()
        page.wait_for_timeout(800)
        page.get_by_text("選択してください", exact=True).last.click()
        page.wait_for_timeout(500)
        page.get_by_role("option", name="ヤマト運輸 - 宅急便", exact=True).click()
        page.wait_for_timeout(1000)
        info = page.evaluate(
            """() => {
              const root = document.querySelector('#modal-root') || document.body;
              const texts = (root.innerText || '').split('\\n').map(t => t.trim()).filter(Boolean).slice(0,120);
              const inputs = Array.from(root.querySelectorAll('input,textarea,select')).map(e => ({
                tag: e.tagName, type: e.type, name: e.name, id: e.id,
                placeholder: e.placeholder, class: (e.className||'').toString().slice(0,80),
                value: e.value,
                label: ((e.closest('label') || e.parentElement || {}).innerText || '').trim().slice(0,60),
                required: e.required, aria: e.getAttribute('aria-label')
              }));
              return {texts, inputs};
            }"""
        )
        path = s.workspace_dir / "buyma" / "shipping_modal_fields.json"
        path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(info["texts"][:80], ensure_ascii=False, indent=2))
        print("--- inputs ---")
        print(json.dumps(info["inputs"], ensure_ascii=False, indent=2)[:4000])
        page.screenshot(path=str(s.workspace_dir / "buyma" / "shipping_modal.png"), full_page=False)
    finally:
        session.close()


if __name__ == "__main__":
    main()
