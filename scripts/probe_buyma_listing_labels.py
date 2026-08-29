"""Probe Buyma listing fields by nearby label text."""

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
    settings = get_settings()
    session = BuymaBrowserSession()
    session.start()
    try:
        assert session.page is not None
        if not session.ensure_logged_in(timeout_seconds=45):
            raise SystemExit("not logged in")
        session.open_new_listing()
        session._dismiss_onboarding()
        mapping = session.page.evaluate(
            """() => {
              const keys = [
                '商品画像','商品名','商品コメント','カテゴリ','ブランド','シーズン','テーマ',
                '色・サイズ','色・サイズ補足','配送方法','購入期限','買付地','買付先ショップ',
                '発送地','商品価格','参考価格','関税','出品メモ','在庫管理'
              ];
              const results = [];
              const all = Array.from(document.querySelectorAll('h2,h3,h4,label,div,span,p,th,dt,legend'));
              for (const key of keys) {
                const hits = [];
                for (const el of all) {
                  const t = (el.innerText || '').trim().split('\\n')[0].trim();
                  if (!t || t.length > 40) continue;
                  if (!t.includes(key)) continue;
                  const root = el.closest('section, .bmm-c-form-group, .bmm-c-panel, form, div') || el.parentElement;
                  const inputs = root ? root.querySelectorAll('input, textarea, select, [contenteditable=true], button') : [];
                  const nearby = [];
                  for (const inp of Array.from(inputs).slice(0, 8)) {
                    nearby.push({
                      tag: inp.tagName,
                      type: inp.type || '',
                      placeholder: inp.placeholder || '',
                      class: (inp.className||'').toString().slice(0,100),
                      text: (inp.innerText||'').trim().slice(0,40),
                      xpath_hint: inp.id || inp.name || inp.placeholder || inp.className
                    });
                  }
                  hits.push({label: t, nearby});
                  if (hits.length >= 3) break;
                }
                results.push({key, hits});
              }
              // Also dump first visible textareas with preceding text
              const tas = [];
              for (const ta of document.querySelectorAll('textarea.bmm-c-textarea')) {
                const rect = ta.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 20) continue;
                let label = '';
                let p = ta.parentElement;
                for (let i=0;i<6 && p;i++) {
                  const h = p.querySelector('h2,h3,h4,label,.bmm-c-form-label');
                  if (h) { label = (h.innerText||'').trim().split('\\n')[0]; break; }
                  p = p.parentElement;
                }
                tas.push({label, h: Math.round(rect.height), w: Math.round(rect.width), y: Math.round(rect.top)});
              }
              return {results, tas};
            }"""
        )
        out = settings.workspace_dir / "buyma" / "listing_label_probe.json"
        out.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out}")
        for ta in mapping.get("tas") or []:
            print("TA", ta)
        for block in mapping.get("results") or []:
            if block.get("hits"):
                print("KEY", block["key"], "->", block["hits"][0].get("label"), "nearby", block["hits"][0].get("nearby")[:3])
    finally:
        session.close()


if __name__ == "__main__":
    main()
