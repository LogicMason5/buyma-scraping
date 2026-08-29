"""Probe Buyma new-listing DOM for form selectors."""

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
        if not session.ensure_logged_in(timeout_seconds=45):
            raise SystemExit("not logged in")
        assert session.page is not None
        session.open_new_listing()
        session._dismiss_onboarding()
        page = session.page
        info = page.evaluate(
            """() => {
              const out = {inputs:[], textareas:[], buttons:[], selects:[], labels:[]};
              for (const el of document.querySelectorAll('input')) {
                out.inputs.push({
                  type: el.type, name: el.name, id: el.id,
                  placeholder: el.placeholder, class: el.className.slice(0,80),
                  aria: el.getAttribute('aria-label'),
                  visible: !!(el.offsetParent || el.getClientRects().length)
                });
              }
              for (const el of document.querySelectorAll('textarea')) {
                out.textareas.push({
                  name: el.name, id: el.id, placeholder: el.placeholder,
                  class: el.className.slice(0,80),
                  visible: !!(el.offsetParent || el.getClientRects().length)
                });
              }
              for (const el of document.querySelectorAll('button, a.bmm-c-button, [role=button]')) {
                const t = (el.innerText || el.textContent || '').trim().replace(/\\s+/g,' ').slice(0,60);
                if (!t) continue;
                out.buttons.push({tag: el.tagName, text: t, class: (el.className||'').toString().slice(0,60)});
              }
              for (const el of document.querySelectorAll('select')) {
                out.selects.push({name: el.name, id: el.id, class: el.className.slice(0,60)});
              }
              for (const el of document.querySelectorAll('label, .bmm-c-form-label, h2, h3, legend')) {
                const t = (el.innerText || '').trim().replace(/\\s+/g,' ').slice(0,80);
                if (t && t.length < 40) out.labels.push(t);
              }
              return out;
            }"""
        )
        out = settings.workspace_dir / "buyma" / "listing_dom_probe.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out}")
        print("visible inputs:", sum(1 for i in info["inputs"] if i.get("visible")))
        print("textareas:", len(info["textareas"]))
        for t in info["textareas"][:10]:
            print(" TA", t)
        for i in info["inputs"]:
            if i.get("visible") and i.get("type") in ("text", "number", "url", "tel", ""):
                print(" IN", i.get("placeholder"), "name=", i.get("name"), "id=", i.get("id"))
        print("buttons sample:")
        for b in info["buttons"][:30]:
            print(" BTN", b["text"])
    finally:
        session.close()


if __name__ == "__main__":
    main()
