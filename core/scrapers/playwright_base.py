from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import BrowserContext, Page

from core.config import get_settings
from core.scrapers.base import BaseScraper, ScrapedProduct
from core.sessions.ec_session_service import (
    apply_saved_cookies,
    has_saved_session,
    save_storage_state,
)
from core.utils.chrome_launch import launch_persistent_chrome, persistent_context_kwargs, wait_out_waf
from core.utils.chrome_profile import prepare_chrome_profile, reset_profile_if_corrupt
from core.utils.playwright_runtime import acquire_playwright, release_playwright

logger = logging.getLogger(__name__)

PRICE_RE = re.compile(
    r"(?P<currency>€|EUR|\$|USD|£|GBP|¥|JPY|円)\s*(?P<amount>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)"
    r"|(?P<amount2>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*(?P<currency2>€|EUR|\$|USD|£|GBP|¥|JPY|円)",
    re.IGNORECASE,
)


class LoginRequiredError(RuntimeError):
    """Raised when member login is required but not confirmed."""


def parse_price_text(raw: str) -> tuple[float, str, str]:
    text = (raw or "").replace("\xa0", " ").strip()
    match = PRICE_RE.search(text)
    if not match:
        digits = re.sub(r"[^\d.,]", "", text)
        amount = _to_float(digits) if digits else 0.0
        currency = "EUR"
        price_text = f"€ {amount:,.2f}" if amount else text
        return amount, currency, price_text

    currency = (match.group("currency") or match.group("currency2") or "EUR").upper()
    if currency in {"€", "EUR"}:
        currency = "EUR"
        symbol = "€"
    elif currency in {"$", "USD"}:
        currency = "USD"
        symbol = "$"
    elif currency in {"£", "GBP"}:
        currency = "GBP"
        symbol = "£"
    elif currency in {"¥", "JPY", "円"}:
        currency = "JPY"
        symbol = "¥"
    else:
        symbol = currency

    amount_raw = match.group("amount") or match.group("amount2") or "0"
    amount = _to_float(amount_raw)
    price_text = f"{symbol} {amount:,.2f}" if currency != "JPY" else f"{symbol} {int(amount):,}"
    return amount, currency, price_text


def _to_float(value: str) -> float:
    cleaned = value.strip()
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        cleaned = cleaned.replace(",", ".") if len(parts[-1]) == 2 else cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _prefer_large_image_url(url: str) -> list[str]:
    """Return candidate URLs from highest quality to lowest for boutique CDNs."""
    if not url:
        return []
    base = url.strip().split("?")[0]
    cands: list[str] = []

    def add(u: str) -> None:
        if u and u not in cands:
            cands.append(u)

    # Julian / Monti style: /mini|/large|/big|/original/conversions/...
    for size in ("original", "big", "large"):
        add(re.sub(r"/(?:mini|micro|medium|large|big|original)/", f"/{size}/", base, count=1, flags=re.I))
    add(base)
    # azureedge vs blob host mirrors
    add(base.replace("julianfashion.azureedge.net", "julianfashionstorage.blob.core.windows.net"))
    add(base.replace("julianfashionstorage.blob.core.windows.net", "julianfashion.azureedge.net"))
    # generic thumb upgrades
    for a, b in (
        ("/thumb/", "/big/"),
        ("/small/", "/big/"),
        ("/medium/", "/big/"),
        ("_thumb", ""),
        ("_small", ""),
        ("w=200", "w=1200"),
        ("w=300", "w=1200"),
        ("w=400", "w=1200"),
    ):
        if a in base:
            add(base.replace(a, b))
    return cands


def save_brand_images(image_urls: list[str], folder: Path) -> list[Path]:
    """Save brand/product gallery images as 1.webp/1.jpg... Prefer original/big over mini thumbs.

    webp is fully supported — tiny files were caused by downloading /mini/ thumbnails,
    not by skipping the webp extension.
    """
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    seen_bytes: set[str] = set()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.julian-fashion.com/",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    min_bytes = 20_000  # reject mini thumbs (~2–5KB)
    with httpx.Client(timeout=40.0, follow_redirects=True, headers=headers) as client:
        index = 1
        for raw_url in image_urls:
            if not raw_url or raw_url.startswith("data:"):
                continue
            got = False
            for url in _prefer_large_image_url(raw_url):
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    body = response.content
                    if len(body) < min_bytes:
                        logger.debug("Skip small image (%s bytes): %s", len(body), url[:140])
                        continue
                    digest = hashlib.sha1(body).hexdigest()
                    if digest in seen_bytes:
                        got = True
                        break
                    content_type = (response.headers.get("content-type") or "").lower()
                    ext = ".jpg"
                    if "png" in content_type or url.lower().endswith(".png") or body[:8] == b"\x89PNG\r\n\x1a\n":
                        ext = ".png"
                    elif (
                        "webp" in content_type
                        or url.lower().endswith(".webp")
                        or body[:4] == b"RIFF"
                    ):
                        ext = ".webp"
                    path = folder / f"{index}{ext}"
                    path.write_bytes(body)
                    saved.append(path)
                    seen_bytes.add(digest)
                    index += 1
                    got = True
                    logger.info("Saved EC image %s (%s bytes) from %s", path.name, len(body), url[:120])
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Image candidate failed %s: %s", url[:120], exc)
            if not got:
                logger.warning("Failed to save usable EC image for %s", (raw_url or "")[:140])
    return saved


STOCK_COUNT_JS = """() => {
  const disabled = (el) => {
    if (!el || el.disabled) return true;
    const cls = (el.className || '').toString().toLowerCase();
    if (/disabled|out-of-stock|sold[- ]?out|unavailable|oos|not-available/.test(cls)) return true;
    if (el.getAttribute('aria-disabled') === 'true') return true;
    if (el.getAttribute('data-stock') === '0') return true;
    return false;
  };
  const parseQtyFromHtml = (html) => {
    const d = document.createElement('div');
    d.innerHTML = html || '';
    const qtyEl = d.querySelector('.js-size-qty, [class*="size-qty"], [data-qty], [data-stock]');
    if (qtyEl) {
      const q = parseInt(
        (qtyEl.getAttribute('data-qty') || qtyEl.getAttribute('data-stock') || qtyEl.textContent || '').trim(),
        10
      );
      if (Number.isFinite(q)) return q;
    }
    const m = String(html || '').match(/(?:qty|quantity|stock|disponibil[ei]|残|在庫)\\s*[:：]?\\s*(\\d+)/i);
    if (m) {
      const q = parseInt(m[1], 10);
      if (Number.isFinite(q)) return q;
    }
    if (/out\\s*of\\s*stock|sold\\s*out|unavailable|esauri|non disponibile/i.test(html || '')) return 0;
    return null;
  };
  const texts = new Set();
  let sumQty = 0;
  let sizedWithQty = 0;
  const push = (t, qty) => {
    const s = (t || '').replace(/\\s+/g, ' ').trim();
    if (!s) return;
    if (/^(select|choose|size|taglia|カラー|色|サイズ|select size)/i.test(s) && s.length < 18) return;
    const key = s.toLowerCase();
    if (texts.has(key)) return;
    texts.add(key);
    if (qty != null && Number.isFinite(qty) && qty > 0) {
      sumQty += qty;
      sizedWithQty += 1;
    } else if (qty == null) {
      // unknown unit qty → count as 1 available unit for that size
      sumQty += 1;
    }
  };
  for (const sel of document.querySelectorAll(
    'select.js-size-choice, select#idTaglia, select[name*="Taglia" i], select[name*="size" i], select[id*="size" i], select[id*="Size"], select'
  )) {
    const name = ((sel.name || '') + ' ' + (sel.id || '') + ' ' + (sel.getAttribute('aria-label') || '')).toLowerCase();
    const nearSize = !!sel.closest('[class*="size" i], [id*="size" i], [class*="Size"]');
    if (!/size|taglia|サイズ|variant|attribute/.test(name) && !nearSize && !sel.classList.contains('js-size-choice')) {
      continue;
    }
    const raw = sel.getAttribute('data-items');
    if (raw) {
      try {
        const items = JSON.parse(raw);
        for (const item of items) {
          if (!item || item.disabled) continue;
          if (item.value === '' || item.value == null) continue;
          const html = item.label || item.text || '';
          const qty = parseQtyFromHtml(html);
          if (qty === 0) continue;
          const d = document.createElement('div');
          d.innerHTML = html;
          const sizeEl = d.querySelector('.size-normal-format, .size-format, [class*="size-normal"]');
          const label = ((sizeEl && sizeEl.textContent) || d.textContent || item.value || '').trim();
          push(label, qty);
        }
      } catch (e) {}
    }
    for (const opt of sel.querySelectorAll('option')) {
      if (!opt.value || disabled(opt)) continue;
      const qty = parseQtyFromHtml(opt.innerHTML || opt.textContent || '');
      if (qty === 0) continue;
      push(opt.textContent, qty);
    }
  }
  const candidates = document.querySelectorAll(
    '[class*="size"] button, [class*="Size"] button, .swatch-option, [data-size], ' +
    'button[data-attribute-code*="size" i], .product-sizes li, .js-size, ' +
    '.product-detail__sizes button, .detail__sizes button, .sizes button'
  );
  for (const el of candidates) {
    if (disabled(el)) continue;
    let qty = null;
    const dq = el.getAttribute('data-qty') || el.getAttribute('data-stock');
    if (dq != null && dq !== '') {
      const q = parseInt(dq, 10);
      if (Number.isFinite(q)) qty = q;
    }
    if (qty === 0) continue;
    push(el.getAttribute('data-size') || el.getAttribute('title') || el.textContent, qty);
  }
  let qtyMax = 0;
  for (const inp of document.querySelectorAll('input[name*="qty" i], input[name*="quantity" i]')) {
    const max = parseInt(inp.getAttribute('max') || '0', 10);
    if (Number.isFinite(max) && max > qtyMax) qtyMax = max;
  }
  // Prefer summed per-size quantities when we saw real size options.
  if (texts.size > 0 && sumQty > 0) return sumQty;
  if (texts.size > 0) return texts.size;
  if (qtyMax > 0) return qtyMax;
  const body = (document.body && document.body.innerText || '').toLowerCase();
  if (/out of stock|sold out|unavailable|non disponibile|esauri/.test(body)) return 0;
  if (document.querySelector(
    'button.add-to-cart, button[name="add"], .js-add-to-cart, form[action*="cart"] button, .product.in-stock'
  )) return 1;
  return 0;
}"""


SIZE_EXTRACT_JS = """() => {
  const sizes = [];
  const push = (t, qty) => {
    let s = (t || '').replace(/\\s+/g, ' ').trim();
    if (!s) return;
    if (/^(select|choose|size|taglia|カラー|色|サイズ|select a size)/i.test(s)) return;
    if (/^\\d{3,}$/.test(s)) return;
    if (qty != null && Number(qty) <= 0) return;
    // Keep canonical size tokens only
    const m = s.match(/^(XXXXL|XXXL|XXL|XL|XXS|XS|S|M|L|FREE(?:\\s*SIZE)?|ONE\\s*SIZE|ONESIZE|UNI|U|\\d{1,2}(?:\\.\\d)?)$/i);
    if (!m) {
      const m2 = s.match(/\\b(XXXXL|XXXL|XXL|XL|XXS|XS|S|M|L|FREE|ONESIZE|UNI|U|\\d{1,2}(?:\\.\\d)?)\\b/i);
      if (!m2) return;
      s = m2[1];
    } else {
      s = m[1];
    }
    s = s.toUpperCase().replace('ONESIZE', 'ONE SIZE').replace(/^FREE$/, 'FREE SIZE');
    if (!sizes.includes(s)) sizes.push(s);
  };

  const parseLabel = (html) => {
    const d = document.createElement('div');
    d.innerHTML = html || '';
    const sizeEl = d.querySelector('.size-normal-format, .size-format, [class*="size-normal"]');
    const sizeText = ((sizeEl && sizeEl.textContent) || '').replace(/\\s+/g, ' ').trim();
    const qtyEl = d.querySelector('.js-size-qty, [class*="size-qty"], [data-qty]');
    let qty = null;
    if (qtyEl) {
      const q = parseInt((qtyEl.textContent || qtyEl.getAttribute('data-qty') || '').trim(), 10);
      if (Number.isFinite(q)) qty = q;
    }
    if (/out\\s*of\\s*stock|sold\\s*out|unavailable|esauri|non disponibile/i.test(html || '')) {
      qty = 0;
    }
    return { sizeText, qty };
  };

  let fromSelect = false;
  for (const sel of document.querySelectorAll(
    'select.js-size-choice, select#idTaglia, select[name*="Taglia" i], select[name*="size" i], select[id*="size" i], select[id*="Size"]'
  )) {
    const raw = sel.getAttribute('data-items');
    if (raw) {
      try {
        const items = JSON.parse(raw);
        for (const item of items) {
          if (!item || item.disabled) continue;
          if (item.value === '' || item.value == null) continue;
          const { sizeText, qty } = parseLabel(item.label || item.text || '');
          if (sizeText) {
            push(sizeText, qty);
            fromSelect = true;
          }
        }
      } catch (e) {}
    }
    for (const opt of sel.querySelectorAll('option')) {
      if (!opt.value || opt.disabled) continue;
      const { sizeText, qty } = parseLabel(opt.innerHTML || opt.textContent || '');
      if (sizeText) {
        push(sizeText, qty);
        fromSelect = true;
      }
    }
  }

  // Only fall back to buttons when no select-based sizes were found.
  if (!fromSelect) {
    for (const el of document.querySelectorAll(
      '.product-detail__sizes button, .detail__sizes button, .product-sizes button, [class*="size"] button.swatch, [data-size]'
    )) {
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
      const cls = (el.className || '').toString().toLowerCase();
      if (/disabled|out-of-stock|sold|unavailable/.test(cls)) continue;
      push(el.getAttribute('data-size') || el.textContent, null);
    }
  }
  return sizes;
}"""


def count_available_inventory(page: Page, *, fallback: int = 0) -> int:
    """Estimate available purchase units on the current PDP.

    Prefers summed per-size quantities when the boutique exposes them; otherwise
    uses the count of available size options. Never silently collapses a multi-size
    product to 1 when sizes are visible.
    """
    n = 0
    try:
        raw = page.evaluate(STOCK_COUNT_JS)
        n = int(raw or 0)
    except Exception:  # noqa: BLE001
        n = 0
    if n <= 0:
        n = max(0, int(fallback or 0))
    try:
        sizes = extract_available_sizes(page)
        if sizes:
            n = max(n, len(sizes))
    except Exception:  # noqa: BLE001
        pass
    return max(0, n)


def extract_available_sizes(page: Page) -> list[str]:
    """Return available size labels from the current PDP (e.g. ['S','M','L'])."""
    try:
        raw = page.evaluate(SIZE_EXTRACT_JS)
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for item in raw:
            s = str(item or "").strip()
            if not s:
                continue
            # Normalize common onesize tokens
            up = s.upper()
            if up in {"U", "UNI", "UNIQUE", "ONE", "ONESIZE", "ONE SIZE", "OS", "FREE", "F"}:
                s = "FREE SIZE" if up in {"FREE", "F", "OS"} else ("U" if up in {"U", "UNI", "UNIQUE"} else "ONE SIZE")
            if s not in out:
                out.append(s)
        return out
    except Exception:  # noqa: BLE001
        return []


@dataclass
class SiteAccount:
    email: str
    password: str


class PlaywrightSiteScraper(BaseScraper):
    listing_urls: list[str] = []
    profile_subdir: str = "default"
    login_url: str | None = None
    require_member_login: bool = True

    def __init__(self) -> None:
        super().__init__()
        self._playwright = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.logged_in = False
        self._headless = False

    def account(self) -> SiteAccount:
        from core.sessions.site_accounts import resolve_site_account

        email, password = resolve_site_account(self.settings, self.site_code)
        return SiteAccount(email=email, password=password)

    def start_browser(self, headless: bool | None = None) -> None:
        """Launch Chrome. Reuses saved session headlessly when available (no re-login UI)."""
        profile = Path(self.settings.chatgpt_profile_path).parent / f"ec-{self.profile_subdir}"
        session_ready = has_saved_session(self.site_code)
        if headless is None:
            # Visible only for first-time login; later runs stay headless for speed.
            headless = session_ready
        self._headless = bool(headless)
        prepare_chrome_profile(profile)
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                self._playwright = acquire_playwright()
                launch_kwargs = persistent_context_kwargs(
                    user_data_dir=str(profile),
                    headless=bool(headless),
                    maximized=not bool(headless),
                )
                self.context = launch_persistent_chrome(self._playwright, **launch_kwargs)
                self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
                applied = apply_saved_cookies(self.context, self.site_code)
                if applied:
                    logger.info("%s applied %s saved session cookies", self.site_code, applied)
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "%s browser launch failed (attempt %s/3): %s",
                    self.site_code,
                    attempt,
                    exc,
                )
                try:
                    if self.context:
                        self.context.close()
                except Exception:  # noqa: BLE001
                    pass
                if self._playwright:
                    release_playwright()
                self.context = None
                self.page = None
                self._playwright = None
                prepare_chrome_profile(profile)
                if attempt == 2:
                    reset_profile_if_corrupt(profile)
                time.sleep(1.2 * attempt)
        raise RuntimeError(f"{self.site_code}: failed to launch Chrome profile: {last_error}")

    def close_browser(self) -> None:
        if self.context:
            try:
                # Only persist a confirmed member session. Saving WAF/guest
                # cookies here used to overwrite a good login with junk.
                if self.logged_in:
                    try:
                        save_storage_state(self.context, self.site_code, login_verified=True)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("%s failed to save session: %s", self.site_code, exc)
                self.context.close()
            except Exception:  # noqa: BLE001
                pass
        if self._playwright:
            release_playwright()
        self.context = None
        self.page = None
        self._playwright = None
        self.logged_in = False

    def login(self) -> None:
        raise NotImplementedError

    def ensure_browser(self) -> Page:
        if not self.page:
            self.start_browser()
        assert self.page is not None
        return self.page

    def persist_session(self) -> None:
        if self.context:
            save_storage_state(self.context, self.site_code, login_verified=True)

    def _prepare_home_for_auth(self, page: Page) -> None:
        page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
        wait_out_waf(page, timeout_ms=45000)
        page.wait_for_timeout(1500)
        self._dismiss_popups(page)
        if any(x in page.url.lower() for x in ["/policy/", "condcookies", "cookie"]):
            page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
            wait_out_waf(page, timeout_ms=20000)
            page.wait_for_timeout(1200)
            self._dismiss_popups(page)

    def is_logged_in(self) -> bool:
        assert self.page is not None
        from core.sessions.login_recipes import member_session_confirmed

        return member_session_confirmed(self.page, self.site_code)

    def ensure_member_login(self, timeout_seconds: int = 180) -> None:
        """Require an existing member session (manual cookie login). Never auto-fill passwords."""
        del timeout_seconds  # reserved; scrape path fails fast when cookies are missing
        page = self.ensure_browser()
        self._prepare_home_for_auth(page)
        if self.is_logged_in():
            self.logged_in = True
            logger.info("%s already logged in as member (cookie/session)", self.site_code)
            self.persist_session()
            return

        if self._headless:
            logger.info("%s headless login check failed; retrying with visible Chrome", self.site_code)
            self.logged_in = False
            self.close_browser()
            self.start_browser(headless=False)
            page = self.ensure_browser()
            self._prepare_home_for_auth(page)
            if self.is_logged_in():
                self.logged_in = True
                logger.info("%s logged in after headed retry", self.site_code)
                self.persist_session()
                return

        raise LoginRequiredError(
            f"{self.site_code}: 未ログインです。パスワード自動入力は行いません。"
            f" 先に手動ログインして cookie を保存してください: "
            f"py -3 scripts/ec_cookie_login.py {self.site_code}"
        )

    def scrape_latest(
        self,
        target_count: int,
        *,
        prefer_new: bool = True,
        skip_keys: set[str] | None = None,
        brand_filter: str | None = None,
        category_filter: str | None = None,
    ) -> Iterable[ScrapedProduct]:
        page = self.ensure_browser()
        if self.require_member_login:
            self.ensure_member_login()
        else:
            self.login()

        collected = 0
        seen: set[str] = set()
        skip_keys = skip_keys or set()
        from core.scrapers.dedupe import scraped_product_keys
        from core.scrapers.filters import card_matches_filters

        for listing in self.listing_urls:
            if collected >= target_count:
                break
            page.goto(listing, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3500)
            self._dismiss_popups(page)

            if self.require_member_login and not (self.logged_in or self.is_logged_in()):
                raise LoginRequiredError(f"{self.site_code}: session lost while browsing listings.")

            cards = self._extract_listing_cards(page)
            for card in cards:
                if collected >= target_count:
                    break
                if not card_matches_filters(card, brand_filter=brand_filter, category_filter=category_filter):
                    continue
                href = self._normalize_product_url((card.get("href") or "").strip())
                if not href or href in seen or not self._is_valid_product_url(href):
                    continue
                seen.add(href)
                if skip_keys and scraped_product_keys(source_url=href) & skip_keys:
                    continue

                # Open detail page while logged-in to capture MEMBER price and all gallery images.
                detail = self._enrich_from_detail(href, card, rank=max(1, target_count - collected))
                if not detail:
                    continue
                if not self._is_valid_product_url(detail.source_url):
                    continue
                if not card_matches_filters(
                    {"brand": detail.brand_name, "name": detail.name, "href": detail.source_url, "category": detail.category or ""},
                    brand_filter=brand_filter,
                    category_filter=category_filter,
                ):
                    continue
                keys = scraped_product_keys(
                    external_id=detail.external_product_id,
                    source_url=detail.source_url,
                )
                if skip_keys and keys & skip_keys:
                    continue
                skip_keys |= keys
                collected += 1
                yield detail
                self.jitter_wait()

        self.close_browser()

    def _normalize_product_url(self, url: str) -> str:
        if not url:
            return ""
        # Drop WAF/tracking query noise; keep clean product path.
        parsed = urlparse(url)
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return clean.rstrip("/")

    def _is_valid_product_url(self, url: str) -> bool:
        if not url:
            return False
        lower = url.lower()
        if any(x in lower for x in ["/customer/account", "/checkout", "/cart", "/login", "/register", "wishlist"]):
            return False
        # Reject old mock placeholders like /product/0 (exact id 0 only).
        if re.search(r"/product/0($|\?|/)", lower):
            return False
        # Accept modern boutique URLs: /product/70330/fendi/coat/...
        if re.search(r"/product/[1-9]\d*(/|$)", lower):
            return True
        # Accept typical Magento / boutique product URLs.
        if lower.endswith(".html"):
            return True
        if re.search(r"/[a-z0-9\-]+/\d+$", lower):
            return True
        if "/catalog/product/view" in lower:
            return True
        return False

    def _enrich_from_detail(self, href: str, card: dict, rank: int) -> ScrapedProduct | None:
        assert self.page is not None
        page = self.page
        page.goto(href, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        self._dismiss_popups(page)

        if self.require_member_login and not (self.logged_in or self.is_logged_in()):
            raise LoginRequiredError(f"{self.site_code}: session lost on product detail. Aborting to avoid guest price.")

        # Reject unavailable / 404-like pages.
        body_lower = ""
        try:
            body_lower = page.inner_text("body").lower()
        except Exception:  # noqa: BLE001
            pass
        if any(
            x in body_lower
            for x in ["page not available", "no longer available", "404", "not found", "articolo non disponibile"]
        ):
            logger.warning("Skipping unavailable product page: %s", href)
            return None

        detail = page.evaluate(
            """() => {
              const text = (sel) => {
                const el = document.querySelector(sel);
                return el ? (el.innerText || '').replace(/\\s+/g, ' ').trim() : '';
              };
              const name = text('h1 .base, h1, .page-title span, .page-title, .product-name, .product-item-name') || document.title;
              const brand = text('.brand, .designer, .product-brand, .manufacturer, [itemprop="brand"]');
              const price = text('.special-price .price, .price-final_price .price, .price-box .price, [itemprop="price"], .price');
              const imgs = Array.from(document.querySelectorAll(
                '.gallery-placeholder img, .fotorama img, .product.media img, .product-image-gallery img, .MagicZoom img, img[src*="photo"], img[src*="media"]'
              ))
                .map(img => img.currentSrc || img.src || '')
                .filter(Boolean);
              const uniq = [];
              for (const u of imgs) if (!uniq.includes(u)) uniq.push(u);
              return { name, brand, price, imgs: uniq.slice(0, 20), href: location.href };
            }"""
        )

        name = (detail.get("name") or card.get("name") or "").strip()
        brand = (detail.get("brand") or card.get("brand") or "").strip()
        price_raw = (detail.get("price") or card.get("price") or "").strip()
        images = detail.get("imgs") or []
        final_url = self._normalize_product_url(page.url or detail.get("href") or href)

        if not name:
            return None
        if any(x in name.lower() for x in ["view all", "login", "register", "page not available"]):
            return None
        if not self._is_valid_product_url(final_url):
            return None
        if not brand and " - " in name:
            brand, maybe = [p.strip() for p in name.split(" - ", 1)]
            if maybe:
                name = maybe
        if not brand:
            brand = "Unknown Brand"

        if not images and card.get("img"):
            images = [card["img"]]

        amount, currency, price_text = parse_price_text(price_raw or "€ 0.00")
        return ScrapedProduct(
            external_product_id=self._external_id_from_url(final_url),
            brand_name=brand,
            name=name,
            source_url=final_url,
            category=None,
            price=amount,
            reference_price=None,
            inventory=count_available_inventory(page, fallback=1 if amount > 0 else 0),
            popularity_rank=rank,
            image_urls=images or None,
            currency=currency,
            price_text=price_text,
        )

    def _dismiss_popups(self, page: Page) -> None:
        """Dismiss Cookiebot / consent overlays that block login and navigation.

        Cookiebot's \"Allow all\" is often an <a>, not a <button>. Only click when the
        consent *dialog* is open — never the \"Your current state: Allow all\" status
        label on the cookie policy page (that navigates away and breaks Monti/Julian).
        """
        dialog = page.locator("#CybotCookiebotDialog:visible, #CookiebotWidget:visible")
        try:
            page.wait_for_selector("#CybotCookiebotDialog:visible, #CookiebotWidget:visible", timeout=3500)
        except Exception:  # noqa: BLE001
            pass

        dialog_open = False
        try:
            dialog_open = dialog.count() > 0 and dialog.first.is_visible()
        except Exception:  # noqa: BLE001
            dialog_open = False

        if dialog_open:
            selectors = [
                "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
                "#CybotCookiebotDialogBodyButtonAccept",
                "#CybotCookiebotDialogBodyLevelButtonAccept",
                "#CybotCookiebotDialog #CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
                "#CybotCookiebotDialog a:has-text('Allow all')",
                "#CybotCookiebotDialog button:has-text('Allow all')",
                "#CybotCookiebotDialog a:has-text('Accept all')",
                "#CybotCookiebotDialog button:has-text('Accept all')",
                "#onetrust-accept-btn-handler",
            ]
            for sel in selectors:
                loc = page.locator(sel).first
                try:
                    if loc.count() == 0 or not loc.is_visible():
                        continue
                    loc.click(timeout=2500, force=True)
                    page.wait_for_timeout(800)
                    break
                except Exception:  # noqa: BLE001
                    continue

            # Scoped role clicks inside the open dialog only.
            try:
                root = page.locator("#CybotCookiebotDialog:visible").first
                if root.count():
                    for label in ["Allow all", "Allow All", "Accept all", "Accept All", "Accept", "Agree", "Accetta"]:
                        for role in ("button", "link"):
                            btn = root.get_by_role(role, name=re.compile(label, re.I))
                            try:
                                if btn.count() == 0:
                                    continue
                                candidate = btn.first
                                if not candidate.is_visible():
                                    continue
                                candidate.click(timeout=2500, force=True)
                                page.wait_for_timeout(800)
                                break
                            except Exception:  # noqa: BLE001
                                continue
            except Exception:  # noqa: BLE001
                pass

            # Last resort: hide blocking dialog so login UI is clickable.
            try:
                if page.locator("#CybotCookiebotDialog:visible").count() > 0:
                    page.evaluate(
                        """() => {
                          for (const id of ['CybotCookiebotDialog', 'CookiebotWidget', 'CybotCookiebotDialogBodyUnderlay']) {
                            const el = document.getElementById(id);
                            if (el) { el.style.display = 'none'; }
                          }
                          document.body.style.overflow = '';
                        }"""
                    )
                    page.wait_for_timeout(300)
            except Exception:  # noqa: BLE001
                pass
            return

        # No Cookiebot dialog — only dismiss generic non-Cookiebot banners.
        # Do NOT match bare "Allow all" text (CookieDeclaration status label).
        for label in ["Accept", "Agree", "Accetta", "I agree", "OK", "Close", "Chiudi"]:
            for role in ("button", "link"):
                btn = page.get_by_role(role, name=re.compile(rf"^{re.escape(label)}$", re.I))
                try:
                    if btn.count() == 0:
                        continue
                    candidate = btn.first
                    if not candidate.is_visible():
                        continue
                    # Skip cookie-policy declaration widgets.
                    el_id = (candidate.get_attribute("id") or "").lower()
                    if "cookiedeclaration" in el_id or "cookieconsent" in el_id:
                        continue
                    candidate.click(timeout=1500)
                    page.wait_for_timeout(400)
                except Exception:  # noqa: BLE001
                    continue

    def _fill_login_form(self, page: Page, email: str, password: str) -> None:
        self._dismiss_popups(page)
        email_input = page.locator(
            "input[type='email']:visible, "
            "input[name*='email' i]:visible, "
            "input[id*='email' i]:visible, "
            "input[autocomplete='username']:visible, "
            "input[autocomplete='email']:visible"
        ).first
        password_input = page.locator(
            "input[type='password']:visible, "
            "input[name*='pass' i]:visible, "
            "input[autocomplete='current-password']:visible"
        ).first
        try:
            email_input.wait_for(state="visible", timeout=12000)
            password_input.wait_for(state="visible", timeout=12000)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"{self.site_code}: login email/password fields not found (cookie banner or wrong page)."
            ) from exc

        email_input.fill(email)
        password_input.fill(password)
        page.wait_for_timeout(400)

        for label in ["Sign in", "Log in", "Login", "LOG IN", "Accedi", "Submit"]:
            for role in ("button", "link"):
                btn = page.get_by_role(role, name=re.compile(label, re.I))
                try:
                    if btn.count() > 0 and btn.first.is_visible():
                        btn.first.click(timeout=4000)
                        page.wait_for_timeout(2500)
                        self._dismiss_popups(page)
                        return
                except Exception:  # noqa: BLE001
                    continue
        submit = page.locator("button[type='submit']:visible, input[type='submit']:visible").first
        if submit.count() > 0:
            submit.click()
            page.wait_for_timeout(2500)
            self._dismiss_popups(page)
            return
        password_input.press("Enter")
        page.wait_for_timeout(2500)
        self._dismiss_popups(page)

    def _extract_listing_cards(self, page: Page) -> list[dict]:
        return page.evaluate(
            """() => {
              const isBad = (href) => {
                const u = (href || '').toLowerCase();
                if (!u) return true;
                if (/\\/product\\/\\d+($|\\?)/.test(u)) return true; // fake placeholder
                if (u.includes('/customer/') || u.includes('/checkout') || u.includes('/cart') || u.includes('/login')) return true;
                return false;
              };
              const roots = Array.from(document.querySelectorAll(
                '.product-item, .product, li.item.product, .product-card, .item.product, [data-product], .products-grid .item, li.product-item'
              ));
              const rows = [];
              const push = (href, name, brand, price, img) => {
                if (!href || !name || isBad(href)) return;
                rows.push({
                  href,
                  name: (name || '').replace(/\\s+/g, ' ').trim(),
                  brand: (brand || '').replace(/\\s+/g, ' ').trim(),
                  price: (price || '').replace(/\\s+/g, ' ').trim(),
                  img: img || ''
                });
              };

              for (const root of roots) {
                const a = root.querySelector('a.product-item-link, a.product-item-photo, a.product-name, a[href$=".html"]');
                if (!a || !a.href) continue;
                const nameEl = root.querySelector('.product-item-name, .product-name, .name, h2, h3');
                const brandEl = root.querySelector('.brand, .designer, .product-brand, .manufacturer');
                const priceEl = root.querySelector('.special-price .price, .price-final_price .price, .price, .price-box .price');
                const imgEl = root.querySelector('img');
                push(
                  a.href,
                  (nameEl && nameEl.innerText) || a.getAttribute('title') || a.innerText || '',
                  (brandEl && brandEl.innerText) || '',
                  (priceEl && priceEl.innerText) || '',
                  imgEl ? (imgEl.currentSrc || imgEl.src || '') : ''
                );
                if (rows.length >= 80) break;
              }

              if (rows.length === 0) {
                for (const a of Array.from(document.querySelectorAll('a.product-item-link, a[href$=".html"]'))) {
                  const href = a.href || '';
                  if (isBad(href)) continue;
                  if (!href.toLowerCase().endsWith('.html') && !/\\/\\d+$/.test(href)) continue;
                  const block = a.closest('li, article, div') || a;
                  const text = (block.innerText || '').replace(/\\s+/g, ' ').trim();
                  if (text.length < 8) continue;
                  const img = block.querySelector('img');
                  const priceMatch = text.match(/(€|EUR|\\$|£)\\s?[0-9][0-9.,]*/);
                  push(href, (a.getAttribute('title') || text.split('\\n')[0]).slice(0, 160), '', priceMatch ? priceMatch[0] : '', img ? (img.currentSrc || img.src || '') : '');
                  if (rows.length >= 80) break;
                }
              }
              return rows;
            }"""
        )

    def _external_id_from_url(self, url: str) -> str:
        path = urlparse(url).path.rstrip("/")
        tail = path.split("/")[-1].replace(".html", "")
        if tail:
            return f"{self.site_code}:{tail}"
        return f"{self.site_code}:{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}"
