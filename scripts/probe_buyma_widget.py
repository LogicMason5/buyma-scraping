"""Tight HTML dump of color/size widget + area radios."""

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
        if not session.ensure_logged_in(timeout_seconds=45):
            raise SystemExit("not logged in")
        page.goto(s.buyma_new_listing_url + "&_fresh=probe5", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        session._dismiss_onboarding()
        session._select_category("レディースファッション ジャケット・アウター ジャケット")
        page.wait_for_timeout(1500)

        dump = page.evaluate(
            """() => {
              // Find smallest element whose direct text context is 色・サイズ heading
              const all = Array.from(document.querySelectorAll('h2,h3,h4,div,section,fieldset'));
              let best = null; let bestScore = 1e18;
              for (const el of all) {
                const kids = Array.from(el.children || []);
                const hasHeading = kids.some(k => ((k.innerText||'').trim().split('\\n')[0]||'').replace(/必須/g,'').trim() === '色・サイズ')
                  || ((el.firstElementChild||{}).innerText||'').trim().startsWith('色・サイズ');
                // looser: class contains color/size/sell
                const cls = (el.className||'').toString();
                const interesting = /sell|color|size|Color|Size/.test(cls);
                if (!hasHeading && !interesting) continue;
                const text = el.innerText || '';
                if (!text.includes('色・サイズ') && !interesting) continue;
                if (!text.includes('色') || text.length > 5000) continue;
                const score = text.length;
                if (score < bestScore) { bestScore = score; best = el; }
              }
              // Also collect all elements with sell- in class
              const sell = Array.from(document.querySelectorAll('[class*=sell-]')).map(e => ({
                class: (e.className||'').toString().slice(0,120),
                text: (e.innerText||'').trim().replace(/\\s+/g,' ').slice(0,80),
                tag: e.tagName
              })).slice(0,80);

              const colorSizeClasses = sell.filter(e => /color|size|Color|Size|spec|Spec/.test(e.class));

              // Find widget by placeholder
              const colorInput = document.querySelector("input[placeholder='色指定なし']");
              let widget = colorInput;
              for (let i=0;i<15 && widget;i++) {
                if ((widget.innerText||'').includes('色・サイズ') || (widget.className||'').toString().includes('sell')) break;
                widget = widget.parentElement;
              }
              return {
                sellSample: sell.slice(0,50),
                colorSizeClasses,
                widgetClass: widget ? (widget.className||'').toString().slice(0,150) : null,
                widgetText: widget ? (widget.innerText||'').slice(0,1200) : null,
                widgetHtml: widget ? widget.innerHTML.slice(0,6000) : null,
                bestClass: best ? (best.className||'').toString().slice(0,150) : null,
                bestText: best ? (best.innerText||'').slice(0,1200) : null,
                bestHtml: best ? best.innerHTML.slice(0,6000) : null,
              };
            }"""
        )
        out = s.workspace_dir / "buyma" / "color_size_widget.json"
        out.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
        print("wrote", out)
        print("sell classes count", len(dump.get("sellSample") or []))
        for e in (dump.get("colorSizeClasses") or [])[:30]:
            print("CS", e)
        print("widgetClass", dump.get("widgetClass"))
        print("widgetText", (dump.get("widgetText") or "")[:800])
        html_path = s.workspace_dir / "buyma" / "color_size_widget.html"
        html_path.write_text(dump.get("widgetHtml") or dump.get("bestHtml") or "", encoding="utf-8")
        print("html", html_path)

        # Area HTML similarly
        area = page.evaluate(
            """() => {
              const sell = Array.from(document.querySelectorAll('[class*=sell-]')).filter(e => /area|Area|ship|Ship|place|Place|region|Region|kaituke|hassou/.test((e.className||'').toString()) || /買付地|発送地/.test(e.innerText||''));
              return sell.slice(0,40).map(e => ({
                class: (e.className||'').toString().slice(0,120),
                text: (e.innerText||'').trim().replace(/\\s+/g,' ').slice(0,100)
              }));
            }"""
        )
        print("area-like sell:", json.dumps(area, ensure_ascii=False, indent=2)[:3000])

        # All unique sell- class tokens
        tokens = page.evaluate(
            """() => {
              const set = new Set();
              for (const el of document.querySelectorAll('[class*=sell-]')) {
                for (const c of (el.className||'').toString().split(/\\s+/)) {
                  if (c.includes('sell-')) set.add(c);
                }
              }
              return [...set].sort();
            }"""
        )
        print("sell tokens:", json.dumps(tokens, ensure_ascii=False, indent=2))
    finally:
        session.close()


if __name__ == "__main__":
    main()
