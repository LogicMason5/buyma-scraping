from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable
from urllib.parse import urlparse

from playwright.sync_api import Page

from core.scrapers.base import ScrapedProduct
from core.scrapers.dedupe import scraped_product_keys
from core.scrapers.playwright_base import (
    PlaywrightSiteScraper,
    count_available_inventory,
    extract_available_sizes,
    parse_price_text,
)

logger = logging.getLogger(__name__)

# Designer index pages (A–Z brand directories).
DESIGNER_INDEX_URLS = [
    "https://www.julian-fashion.com/en-JP/women/designers",
    "https://www.julian-fashion.com/en-JP/men/designers",
]

# Previous-day / new-arrival listings (preferred for daily scrape).
NEW_ARRIVAL_URLS = [
    "https://www.julian-fashion.com/en-JP/women/new",
    "https://www.julian-fashion.com/en-JP/men/new",
]

# Fallback category listings if designer crawl yields nothing.
CATEGORY_FALLBACK_URLS = [
    *NEW_ARRIVAL_URLS,
    "https://www.julian-fashion.com/en-JP/women/clothing",
    "https://www.julian-fashion.com/en-JP/men/clothing",
]


class JulianFashionScraper(PlaywrightSiteScraper):
    """Julian Fashion — brand-first scrape path.

    Site map (verified):
      BRAND nav → /en-JP/{gender}/designers
      Brand PLP → /en-JP/{gender}/designer/{slug}
      Product   → /en-JP/product/{id}/{brand}/{category}/{slug}

    PLP cards use ``div.product.in-stock`` with ``a.js-product`` hrefs
    (not Magento ``.html`` links). PDP: h1=brand, .subtitle=name,
    .js-product-price=member yen price.
    """

    site_code = "julian-fashion"
    site_name = "Julian Fashion"
    base_url = "https://www.julian-fashion.com/en-JP"
    profile_subdir = "julian-fashion"
    # Login is the header SIGN IN dropdown on the top page.
    login_url = "https://www.julian-fashion.com/en-JP/user/login"
    # Prefer brand PLPs; category URLs are fallback only.
    listing_urls = [
        *DESIGNER_INDEX_URLS,
        *CATEGORY_FALLBACK_URLS,
    ]

    def login(self) -> None:
        """Open top-page SIGN IN panel and submit email/password."""
        page = self.ensure_browser()
        account = self.account()
        page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        self._dismiss_popups(page)
        page.wait_for_timeout(800)
        self._dismiss_popups(page)

        opened = self._open_sign_in_panel(page)
        if not opened:
            # Cookie overlay may have blocked the account icon — dismiss again and retry.
            self._dismiss_popups(page)
            opened = self._open_sign_in_panel(page)

        if not opened:
            page.goto(
                "https://www.julian-fashion.com/en-JP/user/login",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(2000)
            self._dismiss_popups(page)

        email_input = page.locator(
            ".sub-menu--user input#login_email:visible, "
            ".sub-menu--user input[name='login_email']:visible, "
            "input#login_email:visible, "
            "input[name='login_email']:visible"
        ).first
        password_input = page.locator(
            ".sub-menu--user input#login_password:visible, "
            ".sub-menu--user input[name='login_password']:visible, "
            "input#login_password:visible, "
            "input[name='login_password']:visible"
        ).first
        try:
            email_input.wait_for(state="visible", timeout=12000)
            password_input.wait_for(state="visible", timeout=12000)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Julian Fashion: SIGN IN email/password fields not found on top page "
                "(Cookiebot overlay may still be blocking)."
            ) from exc

        email_input.fill(account.email)
        password_input.fill(account.password)
        page.wait_for_timeout(400)

        login_btn = page.locator(
            ".sub-menu--user button.js-modal-login:visible, "
            "button.js-modal-login:visible, "
            "button:has-text('LOG IN'):visible, "
            "#user-login-submit:visible"
        ).first
        if login_btn.count() > 0:
            login_btn.click()
        else:
            password_input.press("Enter")

        page.wait_for_timeout(3500)
        self._dismiss_popups(page)

    def _open_sign_in_panel(self, page) -> bool:
        toggles = [
            "li.item.user-menu a.js-toggle",
            "a.js-toggle[aria-label='Log in to your account']",
            ".properties--usermenu a.js-toggle",
            "a[aria-label='Log in to your account']",
            ".user-menu a.js-toggle",
        ]
        for sel in toggles:
            loc = page.locator(sel).first
            try:
                if loc.count() == 0:
                    continue
                loc.scroll_into_view_if_needed(timeout=3000)
                loc.click(timeout=4000, force=True)
                page.wait_for_timeout(1000)
                if self._sign_in_form_visible(page):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return self._sign_in_form_visible(page)

    def _sign_in_form_visible(self, page) -> bool:
        return (
            page.locator("input#login_email:visible, input[name='login_email']:visible").count() > 0
            and page.locator("input#login_password:visible, input[name='login_password']:visible").count() > 0
        )

    def is_logged_in(self) -> bool:
        assert self.page is not None
        from core.sessions.login_recipes import member_session_confirmed

        return member_session_confirmed(self.page, self.site_code)

    def scrape_latest(
        self,
        target_count: int,
        *,
        prefer_new: bool = True,
        skip_keys: set[str] | None = None,
        brand_filter: str | None = None,
        category_filter: str | None = None,
    ) -> Iterable[ScrapedProduct]:
        """New arrivals first (prefer_new), else brand-first crawl. skip_keys dedupes known CSV/workbook items."""
        page = self.ensure_browser()
        if self.require_member_login:
            self.ensure_member_login()
        else:
            self.login()

        collected = 0
        seen: set[str] = set()
        skip_keys = skip_keys or set()
        from core.scrapers.filters import card_matches_filters

        if prefer_new:
            listing_queue = list(NEW_ARRIVAL_URLS)
            logger.info("%s new-arrival queue: %s URLs", self.site_code, len(listing_queue))
            brand_urls: list[str] = []
        else:
            brand_urls = self._collect_brand_urls(page, limit=40)
            listing_queue = brand_urls or list(CATEGORY_FALLBACK_URLS)
            logger.info(
                "%s listing queue: %s brand PLPs (+ fallbacks if empty)",
                self.site_code,
                len(brand_urls),
            )
        if brand_filter:
            # Prefer brand PLPs matching keywords.
            pool = brand_urls or self._collect_brand_urls(page, limit=80)
            matched = [
                u
                for u in pool
                if any(k.lower() in u.lower() for k in brand_filter.replace("、", ",").split(",") if k.strip())
            ]
            if matched:
                listing_queue = matched + [u for u in listing_queue if u not in matched]
                logger.info("%s brand_filter matched %s PLPs", self.site_code, len(matched))
        if category_filter:
            from core.scrapers.site_catalog import category_urls_for

            cat_urls = category_urls_for(
                self.site_code,
                [k.strip() for k in category_filter.replace("、", ",").split(",") if k.strip()],
            )
            if cat_urls:
                listing_queue = cat_urls + [u for u in listing_queue if u not in cat_urls]
                logger.info("%s category_filter matched %s URLs", self.site_code, len(cat_urls))

        for listing in listing_queue:
            if collected >= target_count:
                break
            page.goto(listing, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3500)
            self._dismiss_popups(page)
            self._scroll_listing(page)

            if self.require_member_login and not (self.logged_in or self.is_logged_in()):
                from core.scrapers.playwright_base import LoginRequiredError

                raise LoginRequiredError(f"{self.site_code}: session lost while browsing listings.")

            # Designer index pages list brands, not products — skip product extract.
            if "/designers" in listing.rstrip("/").split("?")[0] and "/designer/" not in listing:
                continue

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

                detail = self._enrich_from_detail(href, card, rank=max(1, target_count - collected))
                if not detail:
                    continue
                if not self._is_valid_product_url(detail.source_url):
                    continue
                if not card_matches_filters(
                    {
                        "brand": detail.brand_name,
                        "name": detail.name,
                        "href": detail.source_url,
                        "category": detail.category or "",
                    },
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

    def _collect_brand_urls(self, page: Page, limit: int = 40) -> list[str]:
        """Read A–Z designer directories and return brand PLP URLs."""
        urls: list[str] = []
        seen: set[str] = set()
        for index_url in DESIGNER_INDEX_URLS:
            page.goto(index_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            self._dismiss_popups(page)
            found = page.evaluate(
                """() => Array.from(document.querySelectorAll('a.designer.js-designer, .designers a[href*="/designer/"]'))
                  .map(a => (a.href || '').split('?')[0])
                  .filter(h => /\\/designer\\/[a-z0-9_]+$/i.test(h))"""
            )
            for href in found or []:
                clean = (href or "").split("?")[0].rstrip("/")
                if not clean or clean in seen:
                    continue
                seen.add(clean)
                urls.append(clean)
                if len(urls) >= limit:
                    return urls
        return urls

    def _scroll_listing(self, page: Page) -> None:
        for _ in range(3):
            try:
                page.mouse.wheel(0, 1600)
                page.wait_for_timeout(800)
            except Exception:  # noqa: BLE001
                break

    def _is_valid_product_url(self, url: str) -> bool:
        if not url:
            return False
        lower = url.lower().split("?", 1)[0]
        if any(x in lower for x in ["/customer/", "/checkout", "/cart", "/login", "/register", "wishlist", "/user/"]):
            return False
        if "/designers" in lower or "/designer/" in lower:
            return False
        # Canonical Julian PDP: /en-JP/product/{id}/{brand}/{cat}/{slug}
        if re.search(r"/product/[1-9]\d+/[^/]+/", lower):
            return True
        return False

    def _extract_listing_cards(self, page: Page) -> list[dict]:
        return page.evaluate(
            """() => {
              const rows = [];
              const seen = new Set();
              const roots = Array.from(document.querySelectorAll('div.product.in-stock, div.product'));
              for (const root of roots) {
                const a = root.querySelector('a.product__photo-wrapper.js-product, a.js-product[href*="/product/"]');
                if (!a || !a.href || !/\\/product\\/[1-9]\\d+\\//.test(a.href)) continue;
                const href = a.href.split('?')[0];
                if (seen.has(href)) continue;
                seen.add(href);

                let brand = '';
                let name = '';
                let price = root.getAttribute('data-origin-price') || '';
                const gtm = root.querySelector('[data-gtm-brand], [data-gtm-name], [data-gtm-price]');
                if (gtm) {
                  brand = gtm.getAttribute('data-gtm-brand') || '';
                  name = gtm.getAttribute('data-gtm-name') || '';
                  if (!price && gtm.getAttribute('data-gtm-price')) {
                    price = '¥ ' + gtm.getAttribute('data-gtm-price');
                  }
                }
                try {
                  const row = root.getAttribute('data-gtm-row');
                  if (row) {
                    const j = JSON.parse(row);
                    brand = brand || j.item_brand || '';
                    name = name || j.item_name || '';
                    if (!price && j.price) price = '¥ ' + j.price;
                  }
                } catch (e) {}

                if (!name) {
                  const t = (root.innerText || '').replace(/\\s+/g, ' ').trim();
                  name = t.slice(0, 160);
                }
                const imgEl = root.querySelector('img.photo, img');
                const img = imgEl ? (imgEl.currentSrc || imgEl.src || '') : '';
                rows.push({
                  href,
                  name: (name || '').replace(/\\s+/g, ' ').trim(),
                  brand: (brand || '').replace(/\\s+/g, ' ').trim(),
                  price: (price || '').replace(/\\s+/g, ' ').trim(),
                  img
                });
                if (rows.length >= 80) break;
              }
              return rows;
            }"""
        )

    def _enrich_from_detail(self, href: str, card: dict, rank: int) -> ScrapedProduct | None:
        assert self.page is not None
        page = self.page
        page.goto(href, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        self._dismiss_popups(page)

        if self.require_member_login and not (self.logged_in or self.is_logged_in()):
            from core.scrapers.playwright_base import LoginRequiredError

            raise LoginRequiredError(f"{self.site_code}: session lost on product detail. Aborting to avoid guest price.")

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

        product_id_match = re.search(r"/product/([1-9]\d+)/", href)
        product_id = product_id_match.group(1) if product_id_match else ""

        detail = page.evaluate(
            """(productId) => {
              const text = (sel) => {
                const el = document.querySelector(sel);
                return el ? (el.innerText || '').replace(/\\s+/g, ' ').trim() : '';
              };
              // h1 = brand; product name is h2.ty-header--tiny (not .subtitle — that is size-guide disclaimer).
              const brand = text('.product-detail__details h1, .product-detail__wrapper h1, h1.ty-header--compact');
              let name = '';
              const nameEl = document.querySelector(
                '.product-detail__details h2.ty-header--tiny, .product-detail__wrapper h2.ty-header--tiny, h2.ty-header.ty-header--tiny.ty-light'
              );
              if (nameEl) name = (nameEl.innerText || '').replace(/\\s+/g, ' ').trim();
              const price = text('.product-detail__price .js-product-price, .js-product-price, .product-detail__price .new');
              const codeMatch = (document.body.innerText || '').match(/cod\\.\\s*([A-Z0-9]+)/i);
              const code = codeMatch ? codeMatch[1] : '';
              const bodyText = document.body.innerText || '';
              let color = '';
              const colorMatch = bodyText.match(/(?:Color|Colour|Colore|カラー)\\s*[:：]\\s*([^\\n|/]+)/i);
              if (colorMatch) color = colorMatch[1].replace(/\\s+/g, ' ').trim();
              const imgs = [];
              const pushUrl = (u) => {
                if (!u || typeof u !== 'string') return;
                const clean = u.trim().split(' ')[0].split('?')[0];
                if (!clean || clean.startsWith('data:')) return;
                if (!clean.includes('julianfashion') && !clean.includes('/product/')) return;
                imgs.push(clean);
              };
              const fromSrcset = (raw) => {
                if (!raw) return;
                let best = '', bestW = -1;
                for (const part of String(raw).split(',')) {
                  const bits = part.trim().split(/\\s+/);
                  if (!bits[0]) continue;
                  const wMatch = (bits[1] || '').match(/(\\d+)w/i);
                  const w = wMatch ? parseInt(wMatch[1], 10) : 0;
                  if (w >= bestW) { bestW = w; best = bits[0]; }
                  pushUrl(bits[0]);
                }
                if (best) pushUrl(best);
              };
              for (const img of Array.from(document.querySelectorAll(
                '.product-detail__photos img, .product-detail__zoom img, img[src*="julianfashionstorage"], img[data-srcset*="julianfashion"], img[content*="product/"]'
              ))) {
                fromSrcset(img.getAttribute('data-srcset') || '');
                fromSrcset(img.srcset || '');
                pushUrl(img.getAttribute('content') || '');
                pushUrl(img.getAttribute('data-src') || '');
                pushUrl(img.currentSrc || img.src || '');
              }
              // Prefer original > big > large; drop mini/thumbs and other product ids.
              const score = (u) => {
                const low = u.toLowerCase();
                if (low.includes('/mini/')) return 1;
                if (low.includes('/large/')) return 2;
                if (low.includes('/big/')) return 4;
                if (low.includes('/original/')) return 6;
                return 3;
              };
              const byKey = new Map();
              for (const u of imgs) {
                if (productId && !u.includes('/product/' + productId + '/')) continue;
                // Group by basename so original wins over mini/big variants of same shot when possible.
                const base = (u.split('/').pop() || u).toLowerCase();
                const prev = byKey.get(base);
                if (!prev || score(u) > score(prev)) byKey.set(base, u);
              }
              // Also keep unique originals even if basenames differ across sizes.
              const ranked = Array.from(new Set([...byKey.values(), ...imgs.filter(u => !productId || u.includes('/product/' + productId + '/'))]))
                .filter(u => {
                  const low = u.toLowerCase();
                  return !low.includes('/mini/') && !low.includes('/micro/') && !low.includes('/medium/') && !low.includes('/large/');
                })
                .sort((a, b) => score(b) - score(a));
              const uniq = [];
              for (const u of ranked) {
                if (!uniq.includes(u)) uniq.push(u);
              }
              const originals = uniq.filter(u => u.toLowerCase().includes('/original/'));
              const finals = originals.length ? originals : uniq.filter(u => u.toLowerCase().includes('/big/'));
              return {
                name, brand, price, code, color,
                imgs: (finals.length ? finals : uniq).slice(0, 12),
                href: location.href
              };
            }""",
            product_id,
        )

        name = (detail.get("name") or card.get("name") or "").strip()
        brand = (detail.get("brand") or card.get("brand") or "").strip()
        price_raw = (detail.get("price") or card.get("price") or "").strip()
        images = detail.get("imgs") or []
        final_url = self._normalize_product_url(page.url or detail.get("href") or href)
        # Prefer live browser URL (post-redirect canonical path) over PLP card href.

        if not name:
            return None
        if any(x in name.lower() for x in ["view all", "login", "register", "page not available"]):
            return None
        if not self._is_valid_product_url(final_url):
            return None
        if not brand:
            brand = "Unknown Brand"

        if not images and card.get("img"):
            images = [card["img"]]

        amount, currency, price_text = parse_price_text(price_raw or "¥ 0")
        sizes = extract_available_sizes(page)
        stock = count_available_inventory(page, fallback=1 if amount > 0 else 0)
        if sizes and stock <= 0:
            stock = len(sizes)
        ext_id = product_id or self._external_id_from_url(final_url)
        color = (detail.get("color") or "").strip()
        if color and (
            color.lower() == (name or "").lower()
            or len(color) > 24
            or re.search(
                r"cardigan|jacket|skirt|boot|bag|shirt|pant|dress|short",
                color,
                re.I,
            )
        ):
            color = ""
        notes_parts: list[str] = []
        if color:
            notes_parts.append(f"Color: {color}")
        if sizes:
            notes_parts.append("Sizes: " + ",".join(sizes))
        notes = "\n".join(notes_parts) if notes_parts else None
        return ScrapedProduct(
            external_product_id=f"{self.site_code}:{ext_id}",
            brand_name=brand,
            name=name,
            source_url=final_url,
            category=None,
            price=amount,
            reference_price=None,
            inventory=stock,
            popularity_rank=rank,
            image_urls=images or None,
            currency=currency,
            price_text=price_text,
            notes=notes,
        )

    def _external_id_from_url(self, url: str) -> str:
        m = re.search(r"/product/([1-9]\d+)/", url)
        if m:
            return m.group(1)
        path = urlparse(url).path.rstrip("/")
        tail = path.split("/")[-1]
        if tail:
            return tail
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
