"""Buyma browser automation: dedicated Chrome profile + cookie auth + listing form fill."""

from __future__ import annotations

import csv
import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import BrowserContext, Locator, Page

from core.config import get_settings
from core.notice_images import ensure_product_notice_images
from core.buyma.buyma_cookie_service import apply_cookies, save_cookies_to_file
from core.buyma.buyma_taxonomy_service import (
    load_brand_aliases,
    load_category_map,
    normalize_brand,
    resolve_category_path,
)
from core.utils.chrome_profile import prepare_chrome_profile
from core.utils.playwright_runtime import acquire_playwright, release_playwright

logger = logging.getLogger(__name__)

# Locator layer — update after probe_buyma_listing.py dumps DOM.
SELECTORS: dict[str, list[str]] = {
    "logged_in_markers": [
        "a[href*='/my/']",
        "a[href*='/my/sell']",
        "text=マイページ",
        "text=出品する",
        "text=出品リスト",
    ],
    "login_markers": [
        "text=ログイン",
        "a[href*='/login']",
        "input[name='email']",
        "input[type='password']",
    ],
    "product_name": [
        "input[name*='item_name']",
        "input[name*='product_name']",
        "input[placeholder*='商品名']",
        "label:has-text('商品名') >> xpath=following::input[1]",
        "#item_name",
        "input[id*='itemName']",
    ],
    "brand_search": [
        "input[placeholder*='ブランド']",
        "input[name*='brand']",
        "label:has-text('ブランド') >> xpath=following::input[1]",
        "#brand_name",
        "input[id*='brand']",
    ],
    "category_open": [
        "text=カテゴリを選択",
        "button:has-text('カテゴリ')",
        "a:has-text('カテゴリ')",
        "[data-testid*='category']",
        "label:has-text('カテゴリ') >> xpath=following::*[self::button or self::a or self::div][1]",
    ],
    "comment": [
        "textarea[name*='comment']",
        "textarea[placeholder*='コメント']",
        "label:has-text('商品コメント') >> xpath=following::textarea[1]",
        "#item_comment",
        "textarea[id*='comment']",
    ],
    "color_size_note": [
        "textarea[name*='size']",
        "textarea[placeholder*='色・サイズ']",
        "label:has-text('色・サイズ') >> xpath=following::textarea[1]",
        "textarea[id*='size']",
    ],
    "price": [
        "input[name*='price']",
        "input[placeholder*='価格']",
        "label:has-text('価格') >> xpath=following::input[1]",
        "#price",
        "input[id*='price']",
    ],
    "reference_price": [
        "input[name*='reference']",
        "input[placeholder*='参考価格']",
        "label:has-text('参考価格') >> xpath=following::input[1]",
    ],
    "purchase_deadline": [
        "input[name*='deadline']",
        "input[placeholder*='購入期限']",
        "label:has-text('購入期限') >> xpath=following::input[1]",
    ],
    "source_url": [
        ".sell-shop-url-table input.bmm-c-text-field",
        "input[placeholder*='URL']",
        "input[type='url']",
    ],
    "stock": [
        ".sell-amount-input input",
        "input[name*='stock']",
        "input[name*='inventory']",
        "label:has-text('買付できる合計数量') >> xpath=following::input[1]",
    ],
    "image_input": [
        "input[type='file'][accept*='image']",
        "input[type='file']",
    ],
    "confirm_button": [
        "button:has-text('入力内容を確認する')",
        "button:has-text('確認画面へ')",
        "button:has-text('確認する')",
        "a:has-text('確認画面へ')",
        "input[type='submit'][value*='確認']",
        "button:has-text('内容を確認')",
    ],
    "submit_button": [
        "button:has-text('注意事項に同意して公開する')",
        "button:has-text('同意して公開する')",
        "button:has-text('出品する')",
        "button:has-text('出品を確定')",
        "button:has-text('この内容で出品')",
        "input[type='submit'][value*='出品']",
        "button:has-text('確定')",
    ],
    "draft_button": [
        "button:has-text('下書き保存')",
        "a:has-text('下書き')",
    ],
}

EXCLUDE_IMAGE_GLOBS = (
    "03_provided_*",
    "04_brand_*",
    "05_provided_*",
    "*brand_intro*",
)


@dataclass
class BuymaListResult:
    success: bool
    error_message: str | None = None
    listed_url: str | None = None
    folder: str | None = None
    steps: list[str] = field(default_factory=list)


class BuymaBrowserSession:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._playwright = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._category_map = load_category_map()
        self._brand_aliases = load_brand_aliases()

    def start(self) -> None:
        profile_path = Path(self.settings.buyma_profile_path)
        prepare_chrome_profile(profile_path)
        self._playwright = acquire_playwright()
        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            channel="chrome",
            headless=False,
            accept_downloads=True,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            viewport=None,
            ignore_default_args=["--enable-automation"],
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        applied = apply_cookies(self.context, self.settings.buyma_cookies_path)
        if applied:
            logger.info("Applied %s Buyma cookies.", applied)
        self.page.goto("https://www.buyma.com/my/", wait_until="domcontentloaded")
        self.page.wait_for_timeout(2500)
        if not self._is_logged_in():
            self.page.reload(wait_until="domcontentloaded")
            self.page.wait_for_timeout(2500)
        ok = self.ensure_logged_in(timeout_seconds=min(60, self.settings.buyma_login_timeout_seconds))
        if not ok:
            logger.warning("Buyma cookie session not ready. Update secrets/buyma_cookies.json.")

    def close(self) -> None:
        if self.context:
            try:
                self.save_session_cookies()
            except Exception:  # noqa: BLE001
                pass
            try:
                self.context.close()
            except Exception:  # noqa: BLE001
                pass
        if self._playwright:
            release_playwright()
        self.context = None
        self.page = None
        self._playwright = None

    def save_session_cookies(self) -> dict[str, Any]:
        if not self.context:
            return {"cookie_count": 0}
        return save_cookies_to_file(self.context, self.settings.buyma_cookies_path)

    def _is_logged_in(self) -> bool:
        assert self.page is not None
        url = self.page.url.lower()
        if "/login" in url or "/register" in url:
            return False
        for sel in SELECTORS["logged_in_markers"]:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                continue
        try:
            body = self.page.inner_text("body")[:4000]
        except Exception:  # noqa: BLE001
            body = ""
        return ("マイページ" in body or "出品する" in body) and "ログイン" not in body[:200]

    def ensure_logged_in(self, timeout_seconds: int = 120) -> bool:
        if not self.page:
            self.start()
        assert self.page is not None
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self._is_logged_in():
                self.save_session_cookies()
                return True
            self.page.wait_for_timeout(1500)
        return False

    def _field_delay(self) -> None:
        lo = float(self.settings.buyma_min_delay_seconds)
        hi = float(self.settings.buyma_max_delay_seconds)
        if hi < lo:
            hi = lo
        time.sleep(random.uniform(lo, hi))

    def _select_delay(self) -> None:
        """Extra settle time after opening/picking React-Select options."""
        lo = float(getattr(self.settings, "buyma_select_delay_min_seconds", 1.2))
        hi = float(getattr(self.settings, "buyma_select_delay_max_seconds", 2.8))
        if hi < lo:
            hi = lo
        time.sleep(random.uniform(lo, hi))

    def between_items_delay(self) -> None:
        lo = float(self.settings.buyma_between_items_min_seconds)
        hi = float(self.settings.buyma_between_items_max_seconds)
        if hi < lo:
            hi = lo
        delay = random.uniform(lo, hi)
        logger.info("Buyma between-items cooldown: %.1fs", delay)
        time.sleep(delay)

    def _first_visible(self, keys: str | list[str]) -> Locator | None:
        assert self.page is not None
        names = keys if isinstance(keys, list) else SELECTORS.get(keys, [keys])
        for sel in names:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=800):
                    return loc
            except Exception:  # noqa: BLE001
                continue
        return None

    def _type_short(self, locator: Locator, text: str, *, clear: bool = True) -> None:
        """Reliable fill for Buyma React text fields (prefer fill over key-by-key)."""
        self._dismiss_onboarding()
        try:
            locator.click(timeout=5000)
        except Exception:  # noqa: BLE001
            locator.click(timeout=5000, force=True)
        try:
            locator.fill(text, timeout=8000)
        except Exception:  # noqa: BLE001
            if clear:
                locator.fill("")
            chunk = max(1, min(4, len(text) // 8 or 1))
            for i in range(0, len(text), chunk):
                locator.type(text[i : i + chunk], delay=random.randint(30, 80))
        # Nudge React controlled inputs
        try:
            locator.evaluate(
                """(el, value) => {
                  const proto = el.tagName === 'TEXTAREA'
                    ? window.HTMLTextAreaElement.prototype
                    : window.HTMLInputElement.prototype;
                  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                  if (desc && desc.set) desc.set.call(el, value);
                  else el.value = value;
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                text,
            )
        except Exception:  # noqa: BLE001
            pass
        self._field_delay()

    def _paste_long(self, locator: Locator, text: str) -> None:
        """Fill long text fields (商品コメント etc.). Prefer fill for React forms."""
        assert self.page is not None
        self._dismiss_onboarding()
        try:
            locator.click(timeout=5000)
        except Exception:  # noqa: BLE001
            locator.click(timeout=5000, force=True)
        try:
            locator.fill(text, timeout=15000)
            locator.evaluate(
                """(el, value) => {
                  const proto = window.HTMLTextAreaElement.prototype;
                  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                  if (desc && desc.set) desc.set.call(el, value);
                  else el.value = value;
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                text,
            )
            self._field_delay()
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Long fill failed, trying clipboard paste: %s", exc)
        try:
            self.page.evaluate(
                """async (t) => {
                  try { await navigator.clipboard.writeText(t); }
                  catch (e) {
                    const ta = document.createElement('textarea');
                    ta.value = t; document.body.appendChild(ta);
                    ta.select(); document.execCommand('copy'); ta.remove();
                  }
                }""",
                text,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Clipboard write failed, falling back to fill: %s", exc)
            locator.fill(text)
            self._field_delay()
            return
        # Select all then paste
        modifier = "Control"
        self.page.keyboard.press(f"{modifier}+A")
        self.page.wait_for_timeout(120)
        self.page.keyboard.press(f"{modifier}+V")
        self.page.wait_for_timeout(300)
        self._field_delay()

    def _locator_near_label(self, label: str, css: str) -> Locator | None:
        """Find the first matching control under the section headed by label text."""
        assert self.page is not None
        idx = self.page.evaluate(
            """({ label, css }) => {
              const heads = Array.from(document.querySelectorAll('h2,h3,h4,label,div,span,p,th,dt,legend'));
              const candidates = [];
              for (const el of heads) {
                const t = (el.innerText || '').trim().split('\\n')[0].trim().replace(/必須/g, '').trim();
                if (!t) continue;
                // Prefer exact / prefix match so we don't hit long comment paragraphs.
                if (t !== label && !t.startsWith(label)) continue;
                if (t.length > label.length + 12) continue;
                let root = el.parentElement;
                for (let depth = 0; depth < 10 && root; depth++) {
                  const list = Array.from(root.querySelectorAll(css)).filter(
                    (e) => e.offsetWidth > 8 && e.offsetHeight > 8
                  );
                  if (list.length) {
                    const all = Array.from(document.querySelectorAll(css));
                    const index = all.indexOf(list[0]);
                    if (index >= 0) {
                      candidates.push({ index, depth, area: root.offsetHeight * root.offsetWidth, t });
                      break;
                    }
                  }
                  root = root.parentElement;
                }
              }
              if (!candidates.length) return -1;
              // Smallest enclosing section wins (avoids matching the whole page).
              candidates.sort((a, b) => (a.area - b.area) || (a.depth - b.depth));
              return candidates[0].index;
            }""",
            {"label": label, "css": css},
        )
        if idx is None or int(idx) < 0:
            return None
        loc = self.page.locator(css).nth(int(idx))
        try:
            if loc.count() > 0:
                return loc
        except Exception:  # noqa: BLE001
            return None
        return None

    def _fill_near_label(
        self,
        label: str,
        value: str,
        *,
        css: str,
        paste: bool = False,
    ) -> bool:
        if value is None or str(value).strip() == "":
            return False
        self._dismiss_onboarding()
        loc = self._locator_near_label(label, css)
        if not loc:
            logger.warning("Buyma label field not found: %s (%s)", label, css)
            return False
        text = str(value)
        if paste or len(text) > 80:
            self._paste_long(loc, text)
        else:
            self._type_short(loc, text)
        return True

    def _fill_by_selectors(self, key: str, value: str, *, paste: bool = False) -> bool:
        if value is None or str(value).strip() == "":
            return False
        # New Buyma UI (sell/new?tab=b): prefer label-relative controls.
        label_map: dict[str, tuple[str, str]] = {
            "product_name": ("商品名", "input.bmm-c-text-field[type='text']"),
            "comment": ("商品コメント", "textarea.bmm-c-textarea"),
            "color_size_note": ("色・サイズ補足情報", "textarea.bmm-c-textarea"),
            "price": ("商品価格", "input.bmm-c-text-field--half-size-char"),
            "purchase_deadline": ("購入期限", "input.sell-term, input.bmm-c-text-field.sell-term"),
            "source_url": ("買付先メモ", ".sell-shop-url-table input.bmm-c-text-field"),
            "stock": ("買付できる合計数量", ".sell-amount-input input, input.bmm-c-text-field"),
        }
        if key in label_map:
            label, css = label_map[key]
            if self._fill_near_label(label, str(value), css=css, paste=paste):
                return True
        loc = self._first_visible(key)
        if not loc:
            logger.warning("Buyma field not found: %s", key)
            return False
        text = str(value)
        if paste or len(text) > 80:
            self._paste_long(loc, text)
        else:
            self._type_short(loc, text)
        return True

    def _click_text_option(self, label: str, *, exact: bool = False) -> bool:
        assert self.page is not None
        if exact:
            try:
                loc = self.page.get_by_text(label, exact=True).first
                if loc.count() > 0 and loc.is_visible(timeout=800):
                    loc.click(timeout=3000)
                    self._field_delay()
                    return True
            except Exception:  # noqa: BLE001
                pass
            # Leaf-node exact click via JS (avoids parent blocks with long innerText).
            clicked = self.page.evaluate(
                """(label) => {
                  const nodes = Array.from(document.querySelectorAll('li,div[role=option],button,a,span,label,td'));
                  for (const el of nodes) {
                    if (!el.offsetParent) continue;
                    const t = (el.innerText || '').trim().split('\\n')[0].trim();
                    if (t === label) { el.click(); return true; }
                  }
                  return false;
                }""",
                label,
            )
            if clicked:
                self._field_delay()
                return True
            return False
        for sel in (
            f"text={label}",
            f"button:has-text('{label}')",
            f"a:has-text('{label}')",
            f"li:has-text('{label}')",
            f"div[role='option']:has-text('{label}')",
            f"label:has-text('{label}')",
        ):
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=600):
                    loc.click(timeout=3000)
                    self._field_delay()
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _open_category_dropdown(self) -> bool:
        """Open the first empty category select (legacy helper)."""
        return self._open_category_select_at(-1)

    def _open_category_select_at(self, index: int) -> bool:
        """Open L1/L2/L3 React-Select by index inside `.sell-category` only.

        ``index < 0`` opens the first still-empty placeholder, else that slot.
        Always re-open L1→L3 by index so 続けて出品する does not keep the previous item's L2/L3.
        """
        assert self.page is not None
        try:
            opened = self.page.evaluate(
                """(index) => {
                  const root = document.querySelector('.sell-category');
                  if (!root) return false;
                  const selects = Array.from(root.querySelectorAll('.Select, .sell-category-select'));
                  if (!selects.length) return false;
                  let target = null;
                  if (index >= 0) {
                    target = selects[Math.min(index, selects.length - 1)] || null;
                  } else {
                    for (const sel of selects) {
                      const ph = sel.querySelector('.Select-placeholder');
                      const txt = ((ph && ph.textContent) || '').trim();
                      if (txt.includes('選択してください')) { target = sel; break; }
                    }
                    if (!target) target = selects[selects.length - 1];
                  }
                  if (!target) return false;
                  const arrow = target.querySelector('.Select-arrow-zone, .Select-arrow, .Select-control');
                  if (!arrow) return false;
                  arrow.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                  arrow.click();
                  return true;
                }""",
                index,
            )
            if opened:
                self.page.wait_for_timeout(500)
                self._select_delay()
                return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("open category select %s failed: %s", index, exc)
        return False

    def _open_select_please(self, *, within: str | None = None) -> bool:
        """Click a visible 『選択してください』 control (optionally scoped)."""
        if within and "sell-category" in within:
            return self._open_category_dropdown()
        assert self.page is not None
        try:
            root = self.page.locator(within) if within else self.page
            loc = root.get_by_text("選択してください", exact=True)
            n = loc.count()
            for i in range(n - 1, -1, -1):
                item = loc.nth(i)
                try:
                    if item.is_visible(timeout=400):
                        item.click(timeout=3000)
                        self._field_delay()
                        return True
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        return False

    def _select_category(self, category: str) -> bool:
        """Select L1→L2→L3 using only `.sell-category` dropdowns (HTML SellUI)."""
        assert self.page is not None
        self._dismiss_onboarding()
        path = resolve_category_path(category, category_map=self._category_map)
        normalized: list[str] = []
        for segment in path:
            if segment in {"ジャケット・アウター", "アウター"}:
                normalized.append("アウター")
            elif segment in {"バッグ"}:
                normalized.append("バッグ・カバン")
            elif segment in {"シューズ"}:
                normalized.append("靴・シューズ")
            elif segment in {"ワンピース・オールインワン"}:
                normalized.append("ワンピース・オールインワン")
            elif segment in {"パーカー・フーディー", "パーカー・フーディ"}:
                normalized.append("パーカー・フーディ")
            else:
                normalized.append(segment)
        path = [s for i, s in enumerate(normalized) if i == 0 or s != normalized[i - 1]]
        if len(path) == 2 and path[1] == "バッグ・カバン":
            path.append("ショルダーバッグ")
        elif len(path) == 2 and path[1] == "靴・シューズ":
            path.append("その他シューズ")
        elif len(path) == 2 and path[1] == "ワンピース・オールインワン":
            path.append("ワンピース")
        elif len(path) < 2:
            path = ["レディースファッション", "アウター", "その他アウター"]

        try:
            self.page.locator(".sell-category").first.scroll_into_view_if_needed(timeout=3000)
        except Exception:  # noqa: BLE001
            pass

        hits = 0
        for idx, segment in enumerate(path):
            if not self._open_category_select_at(idx):
                logger.warning("Category dropdown did not open for: %s", segment)
            aliases = [segment]
            if segment == "レディースファッション":
                aliases = ["レディースファッション", "レディース"]
            elif segment == "バッグ・カバン":
                aliases = ["バッグ・カバン", "バッグ"]
            elif segment == "靴・シューズ":
                aliases = ["靴・シューズ", "シューズ"]
            elif segment == "ファッション小物":
                aliases = ["ファッション小物", "アクセサリー"]
            elif segment == "フラットシューズ":
                aliases = ["フラットシューズ", "バレエシューズ", "その他シューズ"]
            elif segment == "ブーツ":
                aliases = ["ブーツ", "ショートブーツ", "ロングブーツ", "ブーティ", "その他シューズ"]
            elif segment == "ショートパンツ":
                aliases = ["ショートパンツ", "ハーフパンツ", "パンツ"]
            elif segment == "ピアス":
                aliases = ["ピアス", "イヤリング"]
            hit = False
            for alias in aliases:
                try:
                    opt = self.page.locator(
                        ".Select-menu-outer .Select-option, .Select-menu .Select-option"
                    ).filter(has_text=re.compile(rf"^{re.escape(alias)}$|^{re.escape(alias)}\s"))
                    if opt.count() == 0:
                        opt = self.page.locator(".Select-menu-outer, .Select-menu").get_by_text(
                            alias, exact=True
                        )
                    if opt.count() > 0 and opt.first.is_visible(timeout=800):
                        opt.first.click(timeout=2500)
                        hit = True
                        self._select_delay()
                        break
                except Exception:  # noqa: BLE001
                    pass
                if self._pick_react_select_option(alias):
                    hit = True
                    break
                if self._click_text_option(alias, exact=True):
                    hit = True
                    self._select_delay()
                    break
            if not hit:
                for alias in aliases:
                    if self._pick_react_select_option(alias) or self._click_text_option(
                        alias, exact=False
                    ):
                        hit = True
                        self._select_delay()
                        break
            if hit:
                hits += 1
                self.page.wait_for_timeout(750)
                self._select_delay()
            else:
                logger.warning("Category segment not found: %s (path=%s)", segment, path)
                break
        ok = hits >= min(3, len(path))
        try:
            still = self.page.locator(
                ".sell-category .Select-placeholder", has_text="選択してください"
            )
            if still.count() > 0 and still.first.is_visible(timeout=400):
                ok = False
                logger.warning("Category still has unselected dropdown after fill")
        except Exception:  # noqa: BLE001
            pass
        if not ok:
            logger.warning("Category incomplete: selected %s/%s of %s", hits, len(path), path)
        return ok

    def _select_brand(self, brand: str) -> bool:
        resolved = normalize_brand(brand, self._brand_aliases)
        loc = self._first_visible("brand_search")
        if not loc:
            loc = self.page.locator("input[placeholder*='ブランド']").first if self.page else None
        if not loc or loc.count() == 0:
            return self._click_text_option(resolved)
        candidates = []
        for cand in (resolved, brand):
            c = str(cand or "").strip()
            if c and c not in candidates:
                candidates.append(c)
        assert self.page is not None
        for query in candidates:
            try:
                loc.click(timeout=2000)
                loc.fill("")
                self._type_short(loc, query)
                self.page.wait_for_timeout(1100)
                self._select_delay()
            except Exception:  # noqa: BLE001
                continue
            for cand in (query, f"{query}("):
                if self._click_text_option(str(cand).split("(")[0].strip(), exact=False):
                    self._field_delay()
                    return True
            try:
                opt = self.page.locator(".Select-option, [role='option'], li").filter(
                    has_text=re.compile(re.escape(query), re.I)
                )
                if opt.count() > 0 and opt.first.is_visible(timeout=800):
                    opt.first.click(timeout=2500)
                    self._field_delay()
                    return True
            except Exception:  # noqa: BLE001
                pass
        try:
            loc.press("Enter")
            self._field_delay()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _click_radio_label(self, name: str, value: str) -> bool:
        """Click visible bmm radio label (inputs themselves are often not visible)."""
        assert self.page is not None
        try:
            lab = self.page.locator(f"label.bmm-c-radio:has(input[name='{name}'][value='{value}'])")
            if lab.count() > 0:
                lab.first.click(timeout=3000)
                self.page.wait_for_timeout(500)
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _pick_react_select_option(self, label: str) -> bool:
        """Pick an open react-select option by exact/partial text."""
        assert self.page is not None
        if not label:
            return False
        try:
            hit = self.page.evaluate(
                """(label) => {
                  const opts = Array.from(document.querySelectorAll('.Select-option'));
                  const exact = opts.find(e => (e.innerText || '').trim() === label);
                  const partial = opts.find(e => {
                    const t = (e.innerText || '').trim();
                    return t.includes(label) || label.includes(t);
                  });
                  const target = exact || partial;
                  if (!target) return false;
                  target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                  target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                  target.click();
                  return true;
                }""",
                label,
            )
            if hit:
                self.page.wait_for_timeout(450)
                self._select_delay()
                return True
        except Exception:  # noqa: BLE001
            return False
        return False

    def _open_section_select(self, section_title: str, index: int = 0) -> bool:
        """Open the Nth react-select inside a panel section (買付地 / 発送地)."""
        assert self.page is not None
        try:
            # Close any open menus first.
            try:
                self.page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            self.page.wait_for_timeout(150)
            ok = self.page.evaluate(
                """({ section_title, index }) => {
                  const heads = Array.from(document.querySelectorAll('.bmm-c-summary__ttl'));
                  for (const h of heads) {
                    if ((h.innerText || '').trim() !== section_title) continue;
                    const root = h.closest('.bmm-c-panel__item');
                    if (!root) return false;
                    const selects = Array.from(root.querySelectorAll('.Select'));
                    const sel = selects[index];
                    if (!sel) return false;
                    const arrow = sel.querySelector('.Select-arrow-zone, .Select-arrow');
                    const control = sel.querySelector('.Select-control');
                    const target = arrow || control;
                    if (!target) return false;
                    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                    target.click();
                    return sel.classList.contains('is-open') || !!document.querySelector('.Select-option');
                  }
                  return false;
                }""",
                {"section_title": section_title, "index": index},
            )
            self.page.wait_for_timeout(450)
            if not ok:
                # Fallback wait for options
                try:
                    self.page.wait_for_selector(".Select-option", timeout=1500)
                    ok = True
                except Exception:  # noqa: BLE001
                    ok = False
            return bool(ok)
        except Exception:  # noqa: BLE001
            return False

    def _select_area_path(self, section_title: str, path: str) -> bool:
        """Select 買付地/発送地 from settings path like 海外:ヨーロッパ:イタリア or 国内:愛知県."""
        assert self.page is not None
        parts = [p.strip() for p in str(path or "").split(":") if p.strip() and p.strip() != "選択なし"]
        if not parts:
            return False
        region = parts[0]
        rest = parts[1:]
        radio_name = "purchaseArea-region" if section_title == "買付地" else "shippingArea-region"
        region_value = "overseas" if region == "海外" else "domestic" if region == "国内" else ""
        if region_value:
            if not self._click_radio_label(radio_name, region_value):
                self._click_text_option(region, exact=True)
            self.page.wait_for_timeout(600)
        else:
            # Unexpected first token — try as dropdown value later.
            rest = parts

        ok_any = bool(region_value)
        for i, label in enumerate(rest):
            opened = self._open_section_select(section_title, i)
            if not opened:
                logger.warning("Area select not opened: %s #%s", section_title, i)
            if self._pick_react_select_option(label):
                ok_any = True
            else:
                # Retry once after re-open
                self._open_section_select(section_title, i)
                if self._pick_react_select_option(label):
                    ok_any = True
                else:
                    logger.warning("Area option not found: %s / %s", section_title, label)
            self.page.wait_for_timeout(400)
        return ok_any

    def _fetch_source_sizes(self, url: str) -> list[str]:
        """Open EC product URL in a side tab and read available sizes."""
        assert self.context is not None
        u = (url or "").strip()
        if not u.startswith("http"):
            return []
        page = None
        try:
            from core.scrapers.playwright_base import extract_available_sizes

            page = self.context.new_page()
            page.goto(u, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3200)
            try:
                for sel in (
                    "button:has-text('Accept')",
                    "button:has-text('ACCEPT')",
                    "#onetrust-accept-btn-handler",
                ):
                    loc = page.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible(timeout=500):
                        loc.first.click(timeout=1500)
                        page.wait_for_timeout(400)
                        break
            except Exception:  # noqa: BLE001
                pass
            return extract_available_sizes(page)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Source size fetch failed for %s: %s", u, exc)
            return []
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

    def _fetch_source_color(self, url: str) -> str:
        """Open EC product URL in a side tab and read Color: ... (httpx often 403)."""
        assert self.context is not None
        u = (url or "").strip()
        if not u.startswith("http"):
            return ""
        page = None
        try:
            page = self.context.new_page()
            page.goto(u, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3200)
            try:
                # Cookie / region banners on EC sites.
                for sel in (
                    "button:has-text('Accept')",
                    "button:has-text('ACCEPT')",
                    "button:has-text('同意')",
                    "#onetrust-accept-btn-handler",
                ):
                    loc = page.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible(timeout=500):
                        loc.first.click(timeout=1500)
                        page.wait_for_timeout(400)
                        break
            except Exception:  # noqa: BLE001
                pass
            raw = page.evaluate(
                """() => {
                  const text = document.body ? (document.body.innerText || '') : '';
                  const m = text.match(/(?:Color|Colour|Colore|カラー)\\s*[:：]\\s*([^\\n|/]+)/i);
                  return m ? m[1].replace(/\\s+/g, ' ').trim() : '';
                }"""
            )
            out = str(raw or "").strip()
            if not out:
                # Fallback: Python-side regex on inner_text
                body = page.inner_text("body") or ""
                m = re.search(
                    r"(?:Color|Colour|Colore|カラー)\s*[:：]\s*([^\n|/]+)",
                    body,
                    re.I,
                )
                if m:
                    out = re.sub(r"\s+", " ", m.group(1)).strip()
            return out[:60]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Source color fetch failed for %s: %s", u, exc)
            return ""
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

    def _react_set_input(self, css: str, value: str) -> bool:
        """Set a React-controlled text input via native value setter."""
        assert self.page is not None
        try:
            return bool(
                self.page.evaluate(
                    """({ css, value }) => {
                      const inp = document.querySelector(css);
                      if (!inp || inp.disabled) return false;
                      inp.focus();
                      const desc = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                      );
                      if (desc && desc.set) desc.set.call(inp, value);
                      else inp.value = value;
                      inp.dispatchEvent(new Event('input', { bubbles: true }));
                      inp.dispatchEvent(new Event('change', { bubbles: true }));
                      inp.blur();
                      return true;
                    }""",
                    {"css": css, "value": str(value)[:40]},
                )
            )
        except Exception:  # noqa: BLE001
            return False

    def _open_size_variation_select(self) -> bool:
        """Open the バリエーションあり/なし select (not a random Select on the form)."""
        assert self.page is not None
        try:
            opened = self.page.evaluate(
                """() => {
                  const root = document.querySelector('.sell-variation');
                  if (!root) return false;
                  const selects = Array.from(root.querySelectorAll('.Select'));
                  let target = null;
                  for (const sel of selects) {
                    const t = (sel.innerText || '') + ' ' + ((sel.parentElement && sel.parentElement.innerText) || '');
                    if (t.includes('バリエーション') || t.includes('サイズ指定') || t.includes('選択してください')) {
                      target = sel;
                      break;
                    }
                  }
                  if (!target) target = selects[0] || null;
                  if (!target) return false;
                  const arrow = target.querySelector('.Select-arrow-zone, .Select-arrow, .Select-control');
                  if (!arrow) return false;
                  arrow.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                  arrow.click();
                  return true;
                }"""
            )
            if opened:
                self.page.wait_for_timeout(450)
                self._select_delay()
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("open size variation select failed: %s", exc)
        return False

    def _select_color_size(
        self,
        *,
        color: str = "",
        size_text: str = "",
        color_system: str = "",
        color_name: str = "",
    ) -> bool:
        """Select required 色/サイズ controls on the new listing UI (sell-variation)."""
        assert self.page is not None
        self._dismiss_onboarding()
        ok = False

        from core.buyma.buyma_listing_service import (
            _SYSTEM_TO_SHORT_NAME,
            normalize_buyma_sizes,
            sanitize_color_name,
            sizes_prefer_no_variation,
        )

        system = (color_system or color or "").strip()
        if system not in _SYSTEM_TO_SHORT_NAME:
            from core.buyma.buyma_listing_service import infer_color_system

            system = infer_color_system(color_system, f"{color} {color_name}") or system
        fill_name = sanitize_color_name(system, color_name, color)

        # --- Color tab: click system chip only ---
        try:
            tab = self.page.locator(".sell-variation__tab-item", has_text="色")
            if tab.count() > 0:
                tab.first.click(timeout=2000)
                self.page.wait_for_timeout(400)
        except Exception:  # noqa: BLE001
            pass

        try:
            opener = self.page.locator(".sell-color-option").first
            if opener.count() > 0:
                opener.click(timeout=3000)
                self.page.wait_for_timeout(450)
        except Exception:  # noqa: BLE001
            self._click_text_option("色指定なし")

        if system and system not in {"その他", "指定なし"}:
            try:
                name_loc = self.page.locator(".sell-color-option__name", has_text=system)
                if name_loc.count() > 0:
                    name_loc.first.click(timeout=2500)
                    ok = True
                    self.page.wait_for_timeout(500)
                elif self._click_text_option(system, exact=False):
                    ok = True
            except Exception:  # noqa: BLE001
                if self._click_text_option(system, exact=False):
                    ok = True

        if self._react_set_input(
            ".sell-color-table input[type='text']:not([disabled])",
            fill_name,
        ):
            ok = True
            self._field_delay()
        else:
            try:
                name_input = self.page.locator(".sell-color-table input[type='text']:not([disabled])")
                if name_input.count() > 0:
                    name_input.first.fill(fill_name[:40])
                    ok = True
                    self._field_delay()
            except Exception as exc:  # noqa: BLE001
                logger.warning("color name fill failed: %s", exc)

        # --- Size tab ---
        sizes = normalize_buyma_sizes(size_text)
        try:
            size_tab = self.page.locator(".sell-variation__tab-item", has_text="サイズ")
            if size_tab.count() > 0:
                size_tab.first.click(timeout=2500)
                self.page.wait_for_timeout(500)
        except Exception:  # noqa: BLE001
            self._click_text_option("サイズ", exact=True)

        prefer_none = sizes_prefer_no_variation(sizes)
        if self._open_size_variation_select():
            if prefer_none:
                if not self._pick_react_select_option("バリエーションなし"):
                    self._pick_react_select_option("バリエーションあり")
            else:
                if not self._pick_react_select_option("バリエーションあり"):
                    self._pick_react_select_option("バリエーションなし")
                    prefer_none = True
            ok = True
            self.page.wait_for_timeout(700)
            self._select_delay()

        selected_labels: list[str] = []
        if sizes and not prefer_none:
            try:
                picked = self.page.evaluate(
                    """(sizes) => {
                      const root = document.querySelector('.sell-variation') || document;
                      const picked = [];
                      const nodes = Array.from(root.querySelectorAll(
                        'label, tr, li, .sell-size-option, button, [class*="size"]'
                      ));
                      for (const size of sizes) {
                        const exact = nodes.find((el) => {
                          const t = (el.innerText || '').trim().split('\\n')[0].trim();
                          return t === size || t === size + 'サイズ';
                        });
                        const partial = exact || nodes.find((el) => {
                          const t = (el.innerText || '').trim();
                          return t === size || new RegExp('(^|\\\\s)' + size.replace(/[.*+?^${}()|[\\\\]\\\\]/g, '\\\\$&') + '(\\\\s|$)').test(t);
                        });
                        if (!partial) continue;
                        const box = partial.querySelector('input[type=checkbox]')
                          || (partial.matches && partial.matches('input[type=checkbox]') ? partial : null);
                        if (box) {
                          if (!box.checked) {
                            box.click();
                            box.checked = true;
                            box.dispatchEvent(new Event('input', { bubbles: true }));
                            box.dispatchEvent(new Event('change', { bubbles: true }));
                          }
                          picked.push(size);
                        } else {
                          partial.click();
                          picked.push(size);
                        }
                      }
                      return picked;
                    }""",
                    sizes,
                )
                selected_labels = [str(x) for x in (picked or []) if x]
            except Exception as exc:  # noqa: BLE001
                logger.warning("size checkbox JS failed: %s", exc)
            if selected_labels:
                ok = True
                logger.info("Selected Buyma sizes %s (%s matched)", sizes, selected_labels)
            else:
                logger.warning("Could not click size labels on form: %s", sizes)
                if self._open_size_variation_select():
                    self._pick_react_select_option("バリエーションなし")
                    prefer_none = True
                    selected_labels = ["FREE SIZE"]
                    ok = True
                    self.page.wait_for_timeout(500)
        if prefer_none or not sizes:
            selected_labels = selected_labels or ["FREE SIZE"]

        # Size name inputs (required: サイズ名称を入力してください)
        labels = selected_labels or sizes or ["FREE SIZE"]
        try:
            filled_n = self.page.evaluate(
                """(labels) => {
                  const inputs = Array.from(document.querySelectorAll(
                    '.sell-size-table input[type=text]:not([disabled]), '
                    + 'table:has(.sell-size-table) input[type=text]:not([disabled]), '
                    + '.sell-variation__panel input[type=text]:not([disabled])'
                  )).filter((el) => !el.closest('.sell-color-table'));
                  let n = 0;
                  const desc = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                  );
                  for (let i = 0; i < inputs.length; i++) {
                    const label = labels[i] || labels[labels.length - 1] || 'FREE SIZE';
                    const inp = inputs[i];
                    if ((inp.value || '').trim()) continue;
                    inp.focus();
                    if (desc && desc.set) desc.set.call(inp, String(label).slice(0, 40));
                    else inp.value = String(label).slice(0, 40);
                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                    n += 1;
                  }
                  return n;
                }""",
                labels,
            )
            if filled_n:
                ok = True
                logger.info("Filled size name inputs: %s", labels)
                self._field_delay()
        except Exception as exc:  # noqa: BLE001
            logger.warning("size name fill failed: %s", exc)

        return ok

    def _fill_stock_quantity(self, qty: str = "1") -> bool:
        """Fill 買付できる合計数量 (required when color/size variations exist)."""
        assert self.page is not None
        value = str(qty or "1").strip() or "1"
        try:
            filled = self.page.evaluate(
                """(value) => {
                  const input = document.querySelector('.sell-amount-input input, .sell-amount-input .bmm-c-text-field');
                  if (!input) return false;
                  input.focus();
                  const proto = window.HTMLInputElement.prototype;
                  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                  if (desc && desc.set) desc.set.call(input, value);
                  else input.value = value;
                  input.dispatchEvent(new Event('input', { bubbles: true }));
                  input.dispatchEvent(new Event('change', { bubbles: true }));
                  return true;
                }""",
                value,
            )
            if filled:
                # Also try Playwright fill for React sync
                loc = self.page.locator(".sell-amount-input input").first
                if loc.count() > 0:
                    try:
                        loc.fill(value)
                    except Exception:  # noqa: BLE001
                        pass
                self.page.wait_for_timeout(300)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("stock quantity fill failed: %s", exc)
        return False

    def _dismiss_modals(self) -> None:
        """Close leftover Buyma modal overlays that block the main form."""
        assert self.page is not None
        for _ in range(4):
            try:
                overlay = self.page.locator(".bmm-c-modal-overlay, #modal-root .bmm-c-modal-overlay")
                if overlay.count() == 0 or not overlay.first.is_visible(timeout=300):
                    return
            except Exception:  # noqa: BLE001
                return
            # Prefer confirm/save inside modal, else cancel/close.
            for label in ("設定する", "追加する", "保存する", "決定", "閉じる", "キャンセル"):
                if self._click_text_option(label, exact=True):
                    self.page.wait_for_timeout(400)
                    break
            else:
                try:
                    self.page.keyboard.press("Escape")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.page.evaluate(
                        """() => {
                          document.querySelectorAll('.bmm-c-modal-overlay').forEach((el) => el.remove());
                        }"""
                    )
                except Exception:  # noqa: BLE001
                    pass
            self.page.wait_for_timeout(300)

    def _check_shipping_row(self, ship: str = "") -> bool:
        """Check an existing shipping-method row checkbox (required on new UI)."""
        assert self.page is not None
        hints = [h for h in [ship, "ヤマト", "宅急便", "ゆうパック"] if h]
        try:
            checked = self.page.evaluate(
                """(hints) => {
                  const heads = Array.from(document.querySelectorAll('.bmm-c-summary__ttl'));
                  let root = null;
                  for (const h of heads) {
                    if ((h.innerText || '').trim() === '配送方法') {
                      root = h.closest('.bmm-c-panel__item');
                      break;
                    }
                  }
                  if (!root) return false;
                  const rows = Array.from(root.querySelectorAll('.bmm-c-form-table__body tr'));
                  const prefer = hints.length
                    ? rows.filter((tr) => hints.some((h) => (tr.innerText || '').includes(h.replace(/　/g, ' ')) || (tr.innerText || '').includes(h)))
                    : rows;
                  const list = prefer.length ? prefer : rows;
                  for (const tr of list) {
                    const input = tr.querySelector('input[type=checkbox]');
                    if (!input) continue;
                    if (input.checked) return true;
                    // Labels are pointer-none / zero-height; native input.click works.
                    input.click();
                    if (input.checked) return true;
                  }
                  return false;
                }""",
                hints,
            )
            if checked:
                self.page.wait_for_timeout(400)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("shipping row check failed: %s", exc)
        return False

    def _select_shipping_method(self, ship: str) -> bool:
        assert self.page is not None
        self._dismiss_onboarding()

        # Prefer checking an already-registered method (配送方法管理).
        if self._check_shipping_row(ship):
            return True

        if not self._click_text_option("配送方法を追加", exact=True):
            self._click_text_option("+配送方法を追加")
        self.page.wait_for_timeout(700)

        # Open method dropdown inside the shipping-add modal only (not season/theme).
        try:
            modal = self.page.locator("#modal-root, .bmm-c-modal")
            ph = modal.get_by_text("選択してください", exact=True)
            if ph.count() > 0:
                ph.last.click(timeout=3000)
            else:
                ctrl = modal.locator(".Select-control").first
                if ctrl.count() > 0:
                    ctrl.click(timeout=3000)
            self._field_delay()
        except Exception:  # noqa: BLE001
            self._open_select_please(within="#modal-root")

        candidates = [ship] if ship else []
        candidates += [
            "ヤマト運輸 - 宅急便",
            "ヤマト運輸 - 宅急便コンパクト",
            "日本郵便 - ゆうパック",
        ]
        selected = False
        for label in candidates:
            if not label:
                continue
            try:
                opt = self.page.get_by_role("option", name=str(label), exact=True)
                if opt.count() > 0:
                    opt.first.click(timeout=3000)
                    selected = True
                    self._field_delay()
                    break
            except Exception:  # noqa: BLE001
                continue
        if not selected:
            for label in candidates:
                if label and self._click_text_option(str(label), exact=True):
                    selected = True
                    break
        if not selected:
            for label in candidates:
                if label and self._click_text_option(str(label), exact=False):
                    selected = True
                    break

        fee = str(int(getattr(self.settings, "buyma_domestic_shipping_jpy", 1200) or 1200))
        try:
            fee_input = self.page.locator(
                "#modal-root input.bmm-c-text-field--half-size-char, "
                "input.bmm-c-text-field--half-size-char.bmm-c-text-field--size-free"
            ).last
            if fee_input.count() > 0:
                fee_input.click(timeout=3000)
                fee_input.fill(fee)
            frm = self.page.locator("input[name='shipping_date_from']")
            to = self.page.locator("input[name='shipping_date_to']")
            if frm.count() > 0:
                frm.first.click()
                frm.first.fill("7")
            if to.count() > 0:
                to.first.click()
                to.first.fill("14")
            self._click_radio_label("billType", "sender") or self._click_text_option("元払い", exact=True)
            self._click_radio_label("withTracking", "yes") or self._click_text_option("あり", exact=True)
            self._click_radio_label("period_type", "after-order") or self._click_text_option("注文完了後", exact=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("shipping modal fill failed: %s", exc)

        saved = False
        for label in ("設定する", "追加する", "保存する", "決定"):
            try:
                btn = self.page.get_by_role("button", name=label, exact=True)
                if btn.count() > 0 and btn.last.is_visible(timeout=800):
                    btn.last.click(timeout=3000)
                    self.page.wait_for_timeout(900)
                    saved = True
                    break
            except Exception:  # noqa: BLE001
                if self._click_text_option(label, exact=True):
                    self.page.wait_for_timeout(900)
                    saved = True
                    break
        self._dismiss_modals()
        # Newly added row still needs its checkbox checked.
        checked = self._check_shipping_row(ship)
        return checked or selected or saved

    def _count_uploaded_previews(self) -> int:
        """Count Buyma listing image previews.

        Prefer the form's 「残りN枚」 text (max 20). Fallback to
        `.message-gallery__thumb--image` / image_preview URLs.
        """
        assert self.page is not None
        try:
            return int(
                self.page.evaluate(
                    """() => {
                      const maxImages = 20;
                      const text = document.body ? (document.body.innerText || '') : '';
                      const remain = text.match(/残り\\s*(\\d+)\\s*枚/);
                      if (remain) {
                        const left = parseInt(remain[1], 10);
                        if (!Number.isNaN(left) && left >= 0 && left <= maxImages) {
                          return Math.max(0, maxImages - left);
                        }
                      }
                      const thumbs = document.querySelectorAll(
                        '.message-gallery__thumb--image, '
                        + '.message-gallery__thumb.message-gallery__thumb--image, '
                        + 'a.message-gallery__thumb--image'
                      );
                      if (thumbs.length) return thumbs.length;

                      const imgs = Array.from(document.querySelectorAll(
                        '.message-gallery__thumb img, '
                        + 'img[src*="image_preview"], '
                        + 'img[src*="/rorapi/image_preview/"]'
                      ));
                      return imgs.filter((img) => {
                        const src = img.currentSrc || img.src || '';
                        if (!src || src.startsWith('data:image/svg')) return false;
                        return /image_preview|message-gallery/i.test(src)
                          || (img.naturalWidth || img.width || 0) >= 40;
                      }).length;
                    }"""
                )
                or 0
            )
        except Exception:  # noqa: BLE001
            return 0

    def _prepare_buyma_jpeg(self, src: Path, dest: Path, *, max_side: int = 1600, quality: int = 88) -> Path | None:
        """Convert any image to Buyma-friendly JPEG (webp/png often rejected by the form)."""
        try:
            from PIL import Image
            import io

            raw = Path(src).read_bytes()
            img = Image.open(io.BytesIO(raw))
            if img.mode in {"RGBA", "LA", "P"}:
                img = img.convert("RGBA")
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = background
            else:
                img = img.convert("RGB")
            w, h = img.size
            scale = min(1.0, float(max_side) / float(max(w, h) or 1))
            if scale < 1.0:
                img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, format="JPEG", quality=quality, optimize=True)
            if dest.exists() and dest.stat().st_size > 2000:
                return dest
        except Exception as exc:  # noqa: BLE001
            logger.warning("JPEG convert failed for %s: %s", src, exc)
        return None

    def _prepare_upload_files(self, paths: list[Path], work_dir: Path) -> list[Path]:
        """Build ordered JPEG files for Buyma upload."""
        work_dir.mkdir(parents=True, exist_ok=True)
        prepared: list[Path] = []
        for i, src in enumerate(paths):
            if not src.exists():
                continue
            dest = work_dir / f"{i:02d}_{src.stem}.jpg"
            out = self._prepare_buyma_jpeg(src, dest)
            if out:
                prepared.append(out)
                logger.info("Prepared upload %s (%s bytes) from %s", out.name, out.stat().st_size, src.name)
            else:
                logger.warning("Skipped unreadable image: %s", src)
        return prepared

    def _upload_images(self, paths: list[Path]) -> int:
        """Upload images as Buyma-accepted JPEGs and verify preview count.

        Buyma file input accept is image/jpeg,image/gif,image/png — webp is rejected.
        98/99 are often JPEG bytes named .png; real PNG/webp must be converted.
        """
        assert self.page is not None
        if not paths:
            return 0

        import tempfile

        work = Path(tempfile.mkdtemp(prefix="buyma_imgs_"))
        prepared = self._prepare_upload_files(paths, work)
        if not prepared:
            logger.warning("No prepared JPEG images to upload")
            return 0

        before = self._count_uploaded_previews()
        expected = min(len(prepared), int(self.settings.buyma_max_images))
        uploaded = 0

        file_input = None
        for sel in SELECTORS["image_input"]:
            try:
                loc = self.page.locator(sel)
                if loc.count() > 0:
                    file_input = loc.first
                    break
            except Exception:  # noqa: BLE001
                continue
        if file_input is None:
            logger.warning("No image file input found on Buyma listing form.")
            return 0

        # Multi-file JPEG upload first (Buyma accepts multiple on one input).
        try:
            file_input.set_input_files([str(p) for p in prepared[:expected]])
            # Wait until remaining counter / thumbs catch up.
            after = before
            for _ in range(25):
                self.page.wait_for_timeout(400)
                after = self._count_uploaded_previews()
                if after >= before + expected:
                    break
            if after >= before + expected:
                uploaded = after - before
                logger.info("Multi JPEG upload ok: visible=%s (+%s / %s)", after, uploaded, expected)
                return uploaded
            if after > before:
                # Partial success — do NOT re-upload already-accepted files.
                uploaded = after - before
                logger.warning(
                    "Multi upload partial: before=%s after=%s expected=%s; filling remaining one-by-one",
                    before,
                    after,
                    expected,
                )
                prepared = prepared[uploaded:expected]
            else:
                logger.warning(
                    "Multi upload incomplete (before=%s after=%s expected=%s); falling back to one-by-one",
                    before,
                    after,
                    expected,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Multi upload failed: %s", exc)

        for path in prepared:
            if self._count_uploaded_previews() >= before + expected:
                break
            try:
                prev = self._count_uploaded_previews()
                file_input.set_input_files(str(path))
                ok = False
                for _ in range(20):
                    self.page.wait_for_timeout(400)
                    now = self._count_uploaded_previews()
                    if now > prev:
                        ok = True
                        uploaded = now - before
                        break
                if not ok:
                    logger.warning("No preview after uploading %s", path.name)
                else:
                    logger.info("Uploaded %s → previews=%s", path.name, prev + 1)
                self._field_delay()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Upload failed for %s: %s", path.name, exc)

        final = self._count_uploaded_previews()
        uploaded = max(uploaded, final - before)
        logger.info("Image upload finished: prepared=%s visible=%s", expected, uploaded)
        return uploaded

    @staticmethod
    def collect_listing_images(production_dir: Path, max_images: int = 10) -> list[Path]:
        """Buyma upload order: AI(0.jpg/0.png) → 98.png → EC gallery → 99.png last."""
        root = Path(production_dir)
        if not root.is_dir():
            return []
        try:
            ensure_product_notice_images(root)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to seed 98.png / 99.png in %s", root)

        def _is_image(p: Path) -> bool:
            return p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}

        ai = root / "0.jpg"
        if not ai.exists():
            ai = root / "0.png"
        notice_98 = root / "98.png"
        notice_99 = root / "99.png"

        ec_images: list[Path] = []
        for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
            if not _is_image(p):
                continue
            name = p.name.lower()
            if name in {"0.png", "98.png", "99.png"}:
                continue
            if any(Path(name).match(g) for g in EXCLUDE_IMAGE_GLOBS):
                continue
            # Skip broken/tiny EC downloads (old mini thumbs).
            if p.stat().st_size < 12_000:
                continue
            ec_images.append(p)

        # Prefer numeric EC names: 1.webp, 2.jpg...
        def _ec_sort(p: Path) -> tuple[int, str]:
            m = re.match(r"^(\d+)", p.stem)
            return (int(m.group(1)) if m else 10_000, p.name.lower())

        ec_images.sort(key=_ec_sort)

        chosen: list[Path] = []
        if ai.exists() and ai.stat().st_size > 1000:
            chosen.append(ai)
        if notice_98.exists() and notice_98.stat().st_size > 1000 and len(chosen) < max_images:
            chosen.append(notice_98)
        for p in ec_images:
            room_for_99 = 1 if notice_99.exists() else 0
            if len(chosen) >= max_images - room_for_99:
                break
            if p not in chosen:
                chosen.append(p)
        if notice_99.exists() and notice_99.stat().st_size > 1000:
            chosen = [p for p in chosen if p.resolve() != notice_99.resolve()]
            if len(chosen) >= max_images:
                chosen = chosen[: max_images - 1]
            chosen.append(notice_99)
        return chosen[:max_images]

    @staticmethod
    def verify_ec_product_images(production_dir: Path) -> dict[str, Any]:
        """Validate EC gallery files (*.webp etc.) exist and look downloadable."""
        root = Path(production_dir)
        report: dict[str, Any] = {
            "ok": True,
            "count": 0,
            "files": [],
            "warnings": [],
        }
        if not root.is_dir():
            report["ok"] = False
            report["warnings"].append("folder missing")
            return report
        for p in sorted(root.iterdir()):
            if not p.is_file():
                continue
            name = p.name.lower()
            if name in {"0.png", "98.png", "99.png"}:
                continue
            if p.suffix.lower() not in {".webp", ".jpg", ".jpeg", ".png"}:
                continue
            if any(Path(name).match(g) for g in EXCLUDE_IMAGE_GLOBS):
                continue
            size = p.stat().st_size
            report["files"].append({"name": p.name, "size": size})
            report["count"] += 1
            if size < 8_000:
                report["ok"] = False
                report["warnings"].append(f"{p.name} too small ({size} bytes) — likely broken download")
        if report["count"] == 0:
            report["ok"] = False
            report["warnings"].append("no EC product images (*.webp/jpg/png) found")
        return report

    @staticmethod
    def load_listing_row(production_dir: Path) -> dict[str, str]:
        csv_path = Path(production_dir) / "buyma_listing.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"Missing buyma_listing.csv in {production_dir}")
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            row = next(reader, None)
        if not row:
            raise ValueError(f"Empty buyma_listing.csv in {production_dir}")
        # DictReader duplicates keys for duplicate headers — keep last; OK for our use.
        return {str(k): ("" if v is None else str(v)) for k, v in row.items() if k}

    def open_new_listing(self) -> None:
        assert self.page is not None
        # If previous item finished, continue from completion page (required for batch).
        if self._click_continue_listing_if_completed():
            return
        url = self.settings.buyma_new_listing_url
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)
        # Fallback: click 出品する from mypage
        if "sell" not in self.page.url.lower() and "item" not in self.page.url.lower():
            for sel in ("a:has-text('出品する')", "text=新規出品", "a[href*='/my/sell']"):
                try:
                    loc = self.page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        loc.click()
                        self.page.wait_for_timeout(2000)
                        break
                except Exception:  # noqa: BLE001
                    continue
        self._dismiss_onboarding()

    def _is_listing_completed_page(self) -> bool:
        assert self.page is not None
        url = (self.page.url or "").lower()
        if "sell/completed" in url or "/my/sell/complete" in url:
            return True
        try:
            # Prefer a cheap heading check over full body.inner_text (can stall on heavy pages).
            heading = self.page.locator("h1, h2, .page-title, main").first
            text = (heading.inner_text(timeout=800) or "")[:200]
            if "出品完了" in text or "出品が完了" in text:
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            return bool(
                self.page.get_by_text("出品が完了しました", exact=False).count() > 0
                or self.page.get_by_role("button", name=re.compile("続けて出品")).count() > 0
            )
        except Exception:  # noqa: BLE001
            return False

    def _click_continue_listing_if_completed(self) -> bool:
        """On 出品完了 page, click 続けて出品する so the next item can fill a fresh form."""
        assert self.page is not None
        if not self._is_listing_completed_page():
            return False

        clicked = False
        selectors = (
            "button:has-text('続けて出品する')",
            "a:has-text('続けて出品する')",
            "button:has-text('続けて出品')",
            "a:has-text('続けて出品')",
            "text=続けて出品する",
        )
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.count() == 0:
                    continue
                if not loc.is_visible(timeout=1500):
                    continue
                loc.scroll_into_view_if_needed(timeout=2000)
                loc.click(timeout=5000)
                clicked = True
                logger.info("Clicked continue-listing via %s", sel)
                break
            except Exception:  # noqa: BLE001
                continue

        if not clicked:
            for label in ("続けて出品する", "続けて出品"):
                try:
                    btn = self.page.get_by_role("button", name=re.compile(label))
                    if btn.count() == 0:
                        btn = self.page.get_by_role("link", name=re.compile(label))
                    if btn.count() > 0:
                        btn.first.click(timeout=5000)
                        clicked = True
                        logger.info("Clicked '%s' after listing completed", label)
                        break
                except Exception:  # noqa: BLE001
                    continue
                if self._click_text_option(label, exact=False):
                    clicked = True
                    break

        if not clicked:
            # Fallback: go to new listing URL directly.
            try:
                self.page.goto(self.settings.buyma_new_listing_url, wait_until="domcontentloaded")
                self.page.wait_for_timeout(1500)
                self._dismiss_onboarding()
                return True
            except Exception:  # noqa: BLE001
                return False

        # Wait until we leave the completed page / arrive at a new form.
        try:
            self.page.wait_for_url(
                lambda u: "sell/completed" not in (u or "").lower()
                and "/my/sell/complete" not in (u or "").lower(),
                timeout=15000,
            )
        except Exception:  # noqa: BLE001
            self.page.wait_for_timeout(1500)
        self._dismiss_onboarding()
        try:
            self.page.wait_for_selector(".sell-category", timeout=12000)
            self.page.wait_for_timeout(1200)
        except Exception:  # noqa: BLE001
            self.page.wait_for_timeout(1500)
        return True

    def _ensure_duty_included(self) -> bool:
        """Check 「関税込み」 when duty is seller-burdened."""
        assert self.page is not None
        try:
            checked = self.page.evaluate(
                """() => {
                  const labels = Array.from(document.querySelectorAll('label, span, div'));
                  for (const el of labels) {
                    const t = (el.innerText || '').trim();
                    if (!t.includes('関税込み')) continue;
                    const input = el.querySelector('input[type=checkbox]')
                      || (el.htmlFor && document.getElementById(el.htmlFor))
                      || el.closest('label')?.querySelector('input[type=checkbox]');
                    if (input) {
                      if (!input.checked) {
                        (el.closest('label') || el).click();
                      }
                      return !!input.checked || true;
                    }
                    el.click();
                    return true;
                  }
                  return false;
                }"""
            )
            if checked:
                self.page.wait_for_timeout(300)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("duty checkbox JS failed: %s", exc)
        return self._click_text_option("関税込み", exact=False)

    def _collect_validation_hints(self) -> list[str]:
        """Collect real field errors only — ignore always-visible placeholders."""
        assert self.page is not None
        try:
            hints = self.page.evaluate(
                """() => {
                  const out = [];
                  const push = (t) => {
                    const s = (t || '').replace(/\\s+/g, ' ').trim();
                    if (!s || s.length > 120) return;
                    if (s === '選択してください' || s === '入力してください') return;
                    out.push(s);
                  };
                  document.querySelectorAll(
                    '.bmm-c-form-error, .error-message, [class*="form-error"]'
                  ).forEach((el) => {
                    if (el.offsetParent === null) return;
                    push(el.innerText);
                  });
                  const catEmpty = Array.from(
                    document.querySelectorAll('.sell-category .Select-placeholder')
                  ).filter((el) => el.offsetParent && (el.innerText || '').includes('選択してください'));
                  if (catEmpty.length) out.push('カテゴリ未選択');
                  const colorInp = document.querySelector(
                    '.sell-color-table input[type=text]:not([disabled])'
                  );
                  if (colorInp && !(colorInp.value || '').trim()) out.push('色名未入力');
                  return Array.from(new Set(out)).slice(0, 12);
                }"""
            )
            return [str(h) for h in (hints or []) if h]
        except Exception:  # noqa: BLE001
            return []

    def _confirm_modal_open(self) -> bool:
        assert self.page is not None
        try:
            return bool(
                self.page.evaluate(
                    """() => {
                      const root = document.querySelector('#modal-root');
                      const t = (root && root.innerText) || '';
                      return t.includes('出品内容確認') || t.includes('注意事項に同意して公開する');
                    }"""
                )
            )
        except Exception:  # noqa: BLE001
            return False

    def _wait_confirm_modal(self, timeout_ms: int = 8000) -> bool:
        assert self.page is not None
        deadline = time.time() + (timeout_ms / 1000.0)
        while time.time() < deadline:
            if self._confirm_modal_open():
                return True
            self.page.wait_for_timeout(400)
        return False

    def _scroll_to_summary(self, title: str) -> None:
        """Scroll the sell form so the section titled ``title`` is in view."""
        assert self.page is not None
        try:
            self.page.evaluate(
                """(title) => {
                  const els = Array.from(
                    document.querySelectorAll('.bmm-c-summary__ttl, .bmm-c-heading-note__ttl, h1, h2')
                  );
                  for (const el of els) {
                    const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (t === title || t.startsWith(title)) {
                      el.scrollIntoView({ block: 'center', behavior: 'instant' });
                      return true;
                    }
                  }
                  return false;
                }""",
                title,
            )
            self.page.wait_for_timeout(220)
        except Exception:  # noqa: BLE001
            pass

    def _fill_purchase_shop_memo(
        self,
        *,
        shop_name: str = "",
        url: str = "",
        description: str = "",
    ) -> bool:
        """Fill 買付先メモ table (買付先名 / URL / 説明) — current SellUI private fields."""
        assert self.page is not None
        name = (shop_name or "").strip()[:80]
        link = (url or "").strip()[:500]
        desc = (description or "").strip()[:200]
        if not name and not link and not desc:
            return False
        self._scroll_to_summary("買付先メモ")
        try:
            filled = self.page.evaluate(
                """({ name, link, desc }) => {
                  const table = document.querySelector('.sell-shop-url-table');
                  if (!table) return false;
                  const inputs = Array.from(table.querySelectorAll('input.bmm-c-text-field'));
                  if (!inputs.length) return false;
                  const setVal = (input, value) => {
                    if (!input || !value) return;
                    input.focus();
                    const proto = window.HTMLInputElement.prototype;
                    const d = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (d && d.set) d.set.call(input, value);
                    else input.value = value;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                  };
                  if (inputs[0] && name) setVal(inputs[0], name);
                  if (inputs[1] && link) setVal(inputs[1], link);
                  if (inputs[2] && desc) setVal(inputs[2], desc);
                  return true;
                }""",
                {"name": name, "link": link, "desc": desc},
            )
            if filled:
                self.page.wait_for_timeout(300)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("purchase shop memo JS fill failed: %s", exc)
        ok = False
        if name:
            ok = (
                self._fill_near_label(
                    "買付先メモ",
                    name,
                    css=".sell-shop-url-table input.bmm-c-text-field",
                    paste=False,
                )
                or ok
            )
        if link:
            try:
                url_inputs = self.page.locator(".sell-shop-url-table input.bmm-c-text-field")
                if url_inputs.count() >= 2:
                    url_inputs.nth(1).fill(link)
                    ok = True
            except Exception:  # noqa: BLE001
                pass
        return ok

    def _fill_all_listing_fields(self, data: dict[str, Any], *, step: Callable[[str], None]) -> None:
        """Fill SellUI fields top→bottom in page order (images already uploaded).

        Order matches `/my/sell/new` HTML:
        商品名 → コメント → カテゴリ → ブランド → 色・サイズ → 補足 → 配送 →
        購入期限 → 買付地 → ショップ名 → 発送地 → 価格 → 関税 → メモ → 買付先メモ
        """
        from core.buyma.buyma_listing_service import (
            apply_listing_defaults,
            format_video_style_size_text,
            listing_stock_qty,
            refine_listing_category,
            resolve_listing_color,
            resolve_listing_sizes,
        )

        data = apply_listing_defaults(data)
        self._dismiss_onboarding()

        # 1) 商品名
        step("product_name")
        self._scroll_to_summary("商品名")
        title = data.get("商品名") or data.get("product_name") or ""
        if not self._fill_near_label(
            "商品名",
            str(title)[:60],
            css="input.bmm-c-text-field[type='text']:not([placeholder*='ブランド']):not([placeholder*='色'])",
            paste=False,
        ):
            self._fill_by_selectors("product_name", str(title)[:60], paste=False)

        # 2) 商品コメント
        step("comment")
        self._scroll_to_summary("商品コメント")
        comment = data.get("商品コメント") or ""
        self._fill_by_selectors("comment", comment, paste=True)

        # 3) カテゴリ (scoped to .sell-category only)
        step("category")
        self._scroll_to_summary("カテゴリ")
        category = refine_listing_category(data)
        if category:
            data["カテゴリ"] = category
        if category and not self._select_category(str(category)):
            logger.warning("Category select may have failed: %s", category)

        # 4) ブランド
        step("brand")
        self._scroll_to_summary("ブランド")
        brand = data.get("ブランド") or data.get("brand") or ""
        if brand and not self._select_brand(str(brand)):
            logger.warning("Brand select may have failed: %s", brand)

        # Season is optional — leave blank unless provided.
        season = data.get("シーズン") or ""
        if season:
            step("season")
            self._scroll_to_summary("シーズン")
            # Open season select only (first Select after category/brand panels).
            try:
                self.page.evaluate(
                    """() => {
                      const titles = Array.from(document.querySelectorAll('.bmm-c-summary__ttl'));
                      const seasonTitle = titles.find((el) => (el.textContent || '').trim() === 'シーズン');
                      if (!seasonTitle) return false;
                      let root = seasonTitle.closest('.bmm-c-panel__item') || seasonTitle.parentElement;
                      for (let i = 0; i < 6 && root; i++) {
                        const sel = root.querySelector('.Select-control, .Select-arrow-zone');
                        if (sel) { sel.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); sel.click(); return true; }
                        root = root.parentElement;
                      }
                      return false;
                    }"""
                )
                self.page.wait_for_timeout(400)
            except Exception:  # noqa: BLE001
                pass
            self._pick_react_select_option(str(season)) or self._click_text_option(
                str(season), exact=False
            )

        # 5) 色・サイズ
        step("color_size")
        self._scroll_to_summary("色・サイズ")
        color_system, color_name = resolve_listing_color(
            data,
            fetch_color=self._fetch_source_color,
        )
        if color_system:
            data["カラー系統"] = color_system
        if color_name:
            data["色"] = color_name
        sizes = resolve_listing_sizes(data, fetch_sizes=self._fetch_source_sizes)
        if sizes:
            data["サイズ"] = format_video_style_size_text(sizes)
        step(f"color_resolved:{color_system or '-'}|{color_name or '-'}")
        step(f"size_resolved:{','.join(sizes) if sizes else '-'}")
        self._select_color_size(
            color=str(color_system or data.get("カラー系統") or ""),
            color_system=str(color_system or ""),
            color_name=str(color_name or data.get("色") or ""),
            size_text=str(data.get("サイズ") or ""),
        )
        # Stock qty only when SellUI shows `.sell-amount-input`.
        step("stock_qty")
        qty = listing_stock_qty(
            data.get("買付できる合計数量") or data.get("在庫数") or data.get("在庫") or "1",
            sizes,
        )
        step(f"stock_qty_resolved:{qty}")
        self._fill_stock_quantity(str(qty))

        # 6) 色・サイズ補足情報
        step("color_size_note")
        note = data.get("色・サイズ補足情報") or ""
        if note:
            self._scroll_to_summary("色・サイズ")
            self._fill_by_selectors("color_size_note", note, paste=True)

        # 7) 配送方法
        step("shipping")
        self._scroll_to_summary("配送方法")
        ship = data.get("配送方法名") or self.settings.buyma_shipping_method
        self._select_shipping_method(str(ship) if ship else "")

        # 8) 購入期限
        deadline = data.get("購入期限") or ""
        if deadline:
            step("purchase_deadline")
            self._scroll_to_summary("購入期限")
            self._fill_by_selectors("purchase_deadline", str(deadline), paste=False)

        # 9) 買付地
        step("buy_area")
        self._scroll_to_summary("買付地")
        buy_path = data.get("買付地") or self.settings.buyma_procurement_area
        if buy_path:
            self._select_area_path("買付地", str(buy_path))

        # 10) 買付先ショップ名
        shop = (
            data.get("買付先ショップ名")
            or data.get("仕入先保存")
            or data.get("ショップ名")
            or ""
        )
        if shop:
            step("shop_name")
            self._scroll_to_summary("買付先ショップ名")
            self._fill_near_label(
                "買付先ショップ名",
                str(shop)[:30],
                css="input.bmm-c-text-field[type='text']",
                paste=False,
            )

        # 11) 発送地
        step("ship_area")
        self._scroll_to_summary("発送地")
        ship_path = data.get("発送地") or self.settings.buyma_ship_from
        if ship_path:
            self._select_area_path("発送地", str(ship_path))

        # 12) 商品価格
        step("price")
        self._scroll_to_summary("商品価格")
        price = data.get("価格") or data.get("出品価格") or ""
        self._fill_by_selectors("price", str(price), paste=False)

        ref = data.get("参考価格") or ""
        if ref:
            step("reference_price")
            self._scroll_to_summary("参考価格")
            self._click_text_option("参考価格", exact=True)
            self._fill_by_selectors("reference_price", str(ref), paste=False)

        # 13) 関税
        duty = data.get("関税負担") or self.settings.buyma_duty_burden
        if duty:
            step("duty")
            self._scroll_to_summary("関税")
            if not self._ensure_duty_included():
                self._click_text_option(str(duty).split("(")[0].strip() or str(duty))

        # 14) 出品メモ
        memo = data.get("出品メモ") or ""
        if memo:
            step("memo")
            self._scroll_to_summary("出品メモ")
            self._fill_near_label(
                "出品メモ",
                str(memo)[:1000],
                css="textarea.bmm-c-textarea",
                paste=True,
            )

        # 15) 買付先メモ (private) — replaces obsolete 仕入先 / 在庫管理 fields
        source = data.get("仕入先URL") or data.get("買付先URL") or ""
        memo_shop = shop or data.get("買付先名") or ""
        if source or memo_shop:
            step("purchase_shop_memo")
            self._fill_purchase_shop_memo(
                shop_name=str(memo_shop)[:80],
                url=str(source),
                description=str(data.get("買付先説明") or "")[:200],
            )

    def _dismiss_onboarding(self) -> None:
        """Close Buyma driver.js tour / feature popovers that block form clicks."""
        assert self.page is not None
        for _ in range(6):
            acted = False
            for sel in (
                "button:has-text('スキップ')",
                "a:has-text('スキップ')",
                "text=スキップ",
                "button:has-text('閉じる')",
                ".driver-popover-close-btn",
                "[class*='driver-popover'] button:has-text('次へ')",
            ):
                try:
                    loc = self.page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=400):
                        # Prefer Skip over Next to exit the tour quickly
                        if "次へ" in sel:
                            skip = self.page.locator("text=スキップ").first
                            if skip.count() > 0 and skip.is_visible(timeout=200):
                                skip.click(timeout=1500)
                            else:
                                loc.click(timeout=1500)
                        else:
                            loc.click(timeout=1500)
                        acted = True
                        self.page.wait_for_timeout(400)
                        break
                except Exception:  # noqa: BLE001
                    continue
            try:
                overlay = self.page.locator("#driver-page-overlay")
                if overlay.count() > 0 and overlay.is_visible(timeout=300):
                    self.page.keyboard.press("Escape")
                    self.page.wait_for_timeout(200)
                    self.page.evaluate(
                        """() => {
                          document.querySelectorAll(
                            '#driver-page-overlay, .driver-overlay, .driver-popover, .driver-active-element'
                          ).forEach((el) => el.remove());
                          document.body.classList.remove('driver-active', 'driver-fade');
                          document.documentElement.classList.remove('driver-active', 'driver-fade');
                        }"""
                    )
                    acted = True
                    self.page.wait_for_timeout(300)
            except Exception:  # noqa: BLE001
                pass
            if not acted:
                break

    def list_product(
        self,
        production_dir: Path | str,
        row: dict[str, Any] | None = None,
        *,
        on_step: Callable[[str], None] | None = None,
        submit: bool | None = None,
    ) -> BuymaListResult:
        """Fill Buyma new-listing form from a production folder and confirm."""
        steps: list[str] = []

        def step(msg: str) -> None:
            steps.append(msg)
            logger.info("Buyma list: %s", msg)
            if on_step:
                on_step(msg)

        if not self.page:
            self.start()
        assert self.page is not None

        folder = Path(production_dir)
        if not folder.is_dir():
            return BuymaListResult(success=False, error_message=f"Not a directory: {folder}", steps=steps)

        try:
            data = row or self.load_listing_row(folder)
        except Exception as exc:  # noqa: BLE001
            return BuymaListResult(success=False, error_message=str(exc), folder=folder.name, steps=steps)

        if not self._is_logged_in():
            self.page.goto("https://www.buyma.com/my/", wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)
            if not self.ensure_logged_in(timeout_seconds=30):
                return BuymaListResult(
                    success=False,
                    error_message="Buyma not logged in (cookies expired)",
                    folder=folder.name,
                    steps=steps,
                )

        do_submit = self.settings.buyma_auto_submit if submit is None else submit

        try:
            from core.buyma.buyma_listing_service import apply_listing_defaults

            data = apply_listing_defaults(data)

            step("open_new_listing")
            assert self.page is not None
            # Prefer continue-from-completed when batching; else open a fresh form.
            if not self._click_continue_listing_if_completed():
                url = self.settings.buyma_new_listing_url
                sep = "&" if "?" in url else "?"
                self.page.goto(f"{url}{sep}_fresh={int(time.time())}", wait_until="domcontentloaded")
                self.page.wait_for_timeout(2000)
                self._dismiss_onboarding()
                for sel in ("a:has-text('新規出品')", "text=新規出品", "a[href*='/my/sell/new']"):
                    try:
                        loc = self.page.locator(sel).first
                        if loc.count() > 0 and loc.is_visible(timeout=500):
                            loc.click(timeout=3000)
                            self.page.wait_for_timeout(1500)
                            self._dismiss_onboarding()
                            break
                    except Exception:  # noqa: BLE001
                        continue

            # Verify local EC gallery; Buyma upload order is AI → 98 → EC → 99.
            ec_report = self.verify_ec_product_images(folder)
            step(
                f"ec_images_check:count={ec_report['count']} ok={ec_report['ok']} "
                f"warn={';'.join(ec_report['warnings'][:3])}"
            )
            if not ec_report["ok"]:
                logger.warning("EC image check: %s", ec_report["warnings"])

            step("images")
            images = self.collect_listing_images(folder, self.settings.buyma_max_images)
            if not images:
                return BuymaListResult(
                    success=False,
                    error_message="No upload images (need 0.png / 98.png / EC / 99.png)",
                    folder=folder.name,
                    steps=steps,
                )
            step("images_order:" + ",".join(p.name for p in images))
            uploaded = self._upload_images(images)
            step(f"images_uploaded:{uploaded}")
            visible = self._count_uploaded_previews()
            step(f"images_visible:{visible}")
            if visible < len(images):
                return BuymaListResult(
                    success=False,
                    error_message=(
                        f"Image upload incomplete: visible={visible}, "
                        f"prepared={len(images)}, order={[p.name for p in images]}"
                    ),
                    folder=folder.name,
                    listed_url=self.page.url,
                    steps=steps,
                )
            if uploaded < 1 and visible < 1:
                return BuymaListResult(
                    success=False,
                    error_message="Image upload failed",
                    folder=folder.name,
                    listed_url=self.page.url,
                    steps=steps,
                )

            # One-pass fill. Repair only category / color-size when validation hints remain
            # (avoid full double-entry of every field).
            self._fill_all_listing_fields(data, step=step)
            hints = self._collect_validation_hints()
            if hints:
                step("repair_pass:" + "|".join(hints[:4]))
                joined = " ".join(hints)
                if "カテゴリ" in joined:
                    cat = data.get("カテゴリ") or data.get("category") or ""
                    if cat:
                        self._select_category(str(cat))
                if any(k in joined for k in ("サイズ", "色", "カラー")):
                    from core.buyma.buyma_listing_service import resolve_listing_color, resolve_listing_sizes

                    color_system, color_name = resolve_listing_color(
                        data, fetch_color=self._fetch_source_color
                    )
                    sizes = resolve_listing_sizes(data, fetch_sizes=self._fetch_source_sizes)
                    if sizes:
                        from core.buyma.buyma_listing_service import format_video_style_size_text

                        data["サイズ"] = format_video_style_size_text(sizes)
                    self._select_color_size(
                        color=str(color_system or data.get("カラー系統") or ""),
                        color_system=str(color_system or ""),
                        color_name=str(color_name or data.get("色") or ""),
                        size_text=str(data.get("サイズ") or ""),
                    )
                    from core.buyma.buyma_listing_service import listing_stock_qty

                    self._fill_stock_quantity(
                        str(
                            listing_stock_qty(
                                data.get("買付できる合計数量")
                                or data.get("在庫数")
                                or data.get("在庫")
                                or "1",
                                sizes,
                            )
                        )
                    )

            step("confirm")
            self._scroll_to_summary("出品メモ")  # near bottom before confirm bar
            self._dismiss_modals()
            self._field_delay()
            confirm = self._first_visible("confirm_button")
            if confirm:
                try:
                    confirm.scroll_into_view_if_needed(timeout=3000)
                    confirm.click(timeout=5000)
                except Exception:  # noqa: BLE001
                    self._dismiss_modals()
                    try:
                        confirm.click(timeout=5000, force=True)
                    except Exception:  # noqa: BLE001
                        self.page.evaluate(
                            """() => {
                              const btn = Array.from(document.querySelectorAll('button'))
                                .find((b) => (b.innerText || '').trim() === '入力内容を確認する');
                              if (btn) btn.click();
                            }"""
                        )
            reached_confirm = self._wait_confirm_modal(8000)

            # Repair only when Buyma actually reports missing fields. Empty hints +
            # a slow modal used to re-click category/color and make validation worse.
            if not reached_confirm:
                step("confirm_retry")
                hints2 = self._collect_validation_hints()
                joined2 = " ".join(hints2)
                step("confirm_retry_hints:" + "|".join(hints2[:4]))
                repaired = False
                if any(k in joined2 for k in ("カテゴリ", "カテゴリ未選択")):
                    cat = data.get("カテゴリ") or data.get("category") or ""
                    if cat:
                        self._select_category(str(cat))
                        repaired = True
                if any(k in joined2 for k in ("サイズ", "色", "カラー", "色・サイズ")):
                    self._select_color_size(
                        color=str(data.get("カラー系統") or ""),
                        color_system=str(data.get("カラー系統") or ""),
                        color_name=str(data.get("色") or ""),
                        size_text=str(data.get("サイズ") or ""),
                    )
                    repaired = True
                if not repaired:
                    self.page.wait_for_timeout(2500)
                    self._field_delay()
                self._dismiss_modals()
                confirm = self._first_visible("confirm_button")
                if confirm:
                    try:
                        confirm.scroll_into_view_if_needed(timeout=3000)
                        confirm.click(timeout=5000, force=True)
                    except Exception:  # noqa: BLE001
                        pass
                reached_confirm = self._wait_confirm_modal(8000)
            if reached_confirm:
                step("confirm_modal")
            elif do_submit:
                hints_final = self._collect_validation_hints()
                detail = (" | ".join(hints_final[:4])) if hints_final else "validation incomplete"
                return BuymaListResult(
                    success=False,
                    error_message=f"Confirm modal did not open ({detail})",
                    folder=folder.name,
                    listed_url=self.page.url,
                    steps=steps,
                )

            if do_submit:
                step("submit")
                published = False
                try:
                    btn = self.page.get_by_role("button", name="注意事項に同意して公開する", exact=True)
                    if btn.count() > 0:
                        btn.last.click(timeout=8000)
                        published = True
                        self.page.wait_for_timeout(2500)
                except Exception:  # noqa: BLE001
                    published = False
                if not published:
                    submit_btn = self._first_visible("submit_button")
                    if not submit_btn:
                        return BuymaListResult(
                            success=False,
                            error_message="Submit button not found",
                            folder=folder.name,
                            listed_url=self.page.url,
                            steps=steps,
                        )
                    submit_btn.click(timeout=5000)
                    self.page.wait_for_timeout(2500)

                # Wait until completion page, then immediately click 続けて出品する
                # so the UI is not left idle on 出品完了 during between-items cooldown.
                completed = False
                try:
                    self.page.wait_for_url(
                        re.compile(r"sell/completed|/my/sell/complete", re.I),
                        timeout=20000,
                    )
                    completed = True
                except Exception:  # noqa: BLE001
                    for _ in range(15):
                        if self._is_listing_completed_page():
                            completed = True
                            break
                        self.page.wait_for_timeout(500)
                if completed:
                    step("completed_page")
                    if self._click_continue_listing_if_completed():
                        step("continue_listing")
                    else:
                        logger.warning("Could not click 続けて出品する on %s", self.page.url)
                else:
                    logger.warning("Completed page not detected after submit: %s", self.page.url)
                    return BuymaListResult(
                        success=False,
                        error_message="Listing submit did not reach completed page",
                        folder=folder.name,
                        listed_url=self.page.url,
                        steps=steps,
                    )
            else:
                step("submit_skipped")

            self.save_session_cookies()
            step("done")
            return BuymaListResult(
                success=True,
                folder=folder.name,
                listed_url=self.page.url,
                steps=steps,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Buyma list_product failed")
            return BuymaListResult(
                success=False,
                error_message=str(exc),
                folder=folder.name,
                listed_url=self.page.url if self.page else None,
                steps=steps,
            )
