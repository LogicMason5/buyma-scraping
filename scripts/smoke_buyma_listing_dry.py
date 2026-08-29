"""Dry Buyma listing test against https://www.buyma.com/my/sell/new?tab=b (no final submit)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.buyma.buyma_browser_service import BuymaBrowserSession
from core.config import clear_settings_cache, get_settings
from core.csv_schema import read_products_csv
from apps.engine3_buyma.worker import _resolve_production_dir


def main() -> None:
    clear_settings_cache()
    settings = get_settings()
    csv_path = ROOT / "workspace" / "generate" / "products_ready.csv"
    rows = read_products_csv(csv_path)
    if not rows:
        raise SystemExit(f"No rows in {csv_path}")

    row = rows[0]
    folder = _resolve_production_dir(row, generate_root=settings.workspace_dir / "generate", idx=0)
    print(f"Listing URL setting: {settings.buyma_new_listing_url}")
    print(f"Product: {row.get('ブランド')} / {row.get('商品名')}")
    print(f"Folder: {folder}")
    print("Mode: dry fill (submit=False) — goes to confirm if possible, does not click final 出品する")

    session = BuymaBrowserSession()
    session.start()
    try:
        if not session.ensure_logged_in(timeout_seconds=45):
            print("FAIL: not logged in — run: py -3 scripts/buyma_cookie_login.py")
            print(f"Current URL: {session.page.url if session.page else None}")
            raise SystemExit(2)

        print(f"Logged in. URL={session.page.url if session.page else ''}")

        def on_step(msg: str) -> None:
            print(f"  · {msg}")

        result = session.list_product(folder, row=row, on_step=on_step, submit=False)
        print("success=", result.success)
        print("listed_url=", result.listed_url)
        print("error=", result.error_message)
        print("steps=", result.steps)
        if session.page:
            vals = session.page.evaluate(
                """() => {
                  const text = document.body.innerText || '';
                  const nameInput = (() => {
                    const heads = Array.from(document.querySelectorAll('h2,h3,h4,label,div,span'));
                    for (const el of heads) {
                      const t = (el.innerText || '').trim().split('\\n')[0].trim().replace(/必須/g,'').trim();
                      if (t !== '商品名' && !t.startsWith('商品名')) continue;
                      if (t.length > 20) continue;
                      let root = el.parentElement;
                      for (let i=0;i<8 && root;i++) {
                        const input = root.querySelector("input.bmm-c-text-field[type='text']");
                        if (input && input.offsetParent) return input.value;
                        root = root.parentElement;
                      }
                    }
                    return null;
                  })();
                  const brand = (document.querySelector("input[placeholder*='ブランド']") || {}).value || null;
                  const price = (document.querySelector("input.bmm-c-text-field--half-size-char") || {}).value || null;
                  const errors = Array.from(document.querySelectorAll('[class*=error], .bmm-c-text--error'))
                    .map(e => (e.innerText||'').trim()).filter(t => t && t.length < 80).slice(0,20);
                  return {
                    nameInput, brand, price, errors, url: location.href,
                    hasJacket: text.includes('ジャケット'),
                    hasOuter: text.includes('アウター'),
                    hasYamato: text.includes('ヤマト'),
                    hasConfirmTitle: text.includes('出品内容確認') || text.includes('注意事項に同意して公開する') || text.includes('出品内容の確認'),
                    hasSizeError: text.includes('サイズを選択'),
                    hasShipError: text.includes('配送方法を最低'),
                    hasAreaError: text.includes('発送地域を入力'),
                    hasColorError: text.includes('色名称を入力'),
                    hasAichi: text.includes('愛知'),
                    hasItaly: text.includes('イタリア'),
                    hasFreeSize: text.includes('FREE SIZE') || text.includes('バリエーションなし'),
                    shipChecked: !!document.querySelector('.bmm-c-form-table__body input[type=checkbox]:checked'),
                    stockQty: (document.querySelector('.sell-amount-input input') || {}).value || null,
                    modalButtons: Array.from(document.querySelectorAll('#modal-root button')).map(b => (b.innerText||'').trim()).filter(Boolean),
                  };
                }"""
            )
            print("verify=", vals)
            shot = settings.workspace_dir / "buyma" / "listing_dry_test.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            session.page.screenshot(path=str(shot), full_page=True)
            print(f"screenshot={shot}")
            session.page.wait_for_timeout(5000)
    finally:
        session.close()


if __name__ == "__main__":
    main()
