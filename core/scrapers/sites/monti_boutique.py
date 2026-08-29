from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable
from urllib.parse import urlparse

from playwright.sync_api import Page

from core.scrapers.base import ScrapedProduct
from core.scrapers.dedupe import scraped_product_keys
from core.scrapers.playwright_base import LoginRequiredError, PlaywrightSiteScraper, count_available_inventory, extract_available_sizes, parse_price_text

logger = logging.getLogger(__name__)

DESIGNER_INDEX_URLS = [
    "https://www.montiboutique.com/en-JP/women/designers",
    "https://www.montiboutique.com/en-JP/men/designers",
]

NEW_ARRIVAL_URLS = [
    "https://www.montiboutique.com/en-JP/women/new",
    "https://www.montiboutique.com/en-JP/men/new",
]

CATEGORY_FALLBACK_URLS = [
    *NEW_ARRIVAL_URLS,
]


class MontiBoutiqueScraper(PlaywrightSiteScraper):
    """Monti Boutique — same platform family as Julian; brand-first en-JP path.

    DESIGNERS → /en-JP/{gender}/designers
    Brand PLP → /en-JP/{gender}/designer/{slug}
    PDP → /en-JP/product/{id}/{brand}/{cat}/{slug}
    """

    site_code = "montiboutique"
    site_name = "Monti Boutique"
    base_url = "https://www.montiboutique.com/en-JP"
    profile_subdir = "montiboutique"
    login_url = "https://www.montiboutique.com/en-JP/user/login"
    require_member_login = True
    listing_urls = [*DESIGNER_INDEX_URLS, *CATEGORY_FALLBACK_URLS]

    def login(self) -> None:
        """Open user login panel (same platform family as Julian Fashion)."""
        page = self.ensure_browser()
        account = self.account()
        page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        self._dismiss_popups(page)
        for trigger in (
            "a[href*='/user/login']",
            "button:has-text('Sign in')",
            "button:has-text('Log in')",
            ".sub-menu--user",
        ):
            loc = page.locator(trigger).first
            try:
                if loc.count() > 0:
                    loc.click(timeout=4000, force=True)
                    page.wait_for_timeout(1500)
                    break
            except Exception:  # noqa: BLE001
                continue
        if page.locator("input#login_email:visible, input[name='login_email']:visible").count() == 0:
            page.goto(self.login_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            self._dismiss_popups(page)

        email_input = page.locator(
            ".sub-menu--user input#login_email:visible, "
            "input#login_email:visible, input[name='login_email']:visible"
        ).first
        password_input = page.locator(
            ".sub-menu--user input#login_password:visible, "
            "input#login_password:visible, input[name='login_password']:visible"
        ).first
        email_input.wait_for(state="visible", timeout=12000)
        password_input.wait_for(state="visible", timeout=12000)
        email_input.fill(account.email)
        password_input.fill(account.password)
        login_btn = page.locator(
            ".sub-menu--user button.js-modal-login:visible, "
            "button.js-modal-login:visible, #user-login-submit:visible"
        ).first
        if login_btn.count() > 0:
            login_btn.click()
        else:
            password_input.press("Enter")
        page.wait_for_timeout(3500)
        page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
        self._dismiss_popups(page)

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
        page = self.ensure_browser()
        self.ensure_member_login()
        if "/en-JP" not in page.url:
            page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            self._dismiss_popups(page)

        collected = 0
        seen: set[str] = set()
        skip_keys = skip_keys or set()
        from core.scrapers.filters import card_matches_filters

        brand_urls: list[str] = []
        if prefer_new:
            listing_queue = list(NEW_ARRIVAL_URLS)
            logger.info("%s new-arrival queue: %s URLs", self.site_code, len(listing_queue))
        else:
            brand_urls = self._collect_brand_urls(page, limit=40)
            listing_queue = brand_urls or list(CATEGORY_FALLBACK_URLS)
            logger.info("%s listing queue: %s brand PLPs", self.site_code, len(brand_urls))
        if brand_filter:
            pool = brand_urls or self._collect_brand_urls(page, limit=80)
            matched = [
                u
                for u in pool
                if any(k.lower() in u.lower() for k in brand_filter.replace("、", ",").split(",") if k.strip())
            ]
            if matched:
                listing_queue = matched + [u for u in listing_queue if u not in matched]
        if category_filter:
            from core.scrapers.site_catalog import category_urls_for

            cat_urls = category_urls_for(
                self.site_code,
                [k.strip() for k in category_filter.replace("、", ",").split(",") if k.strip()],
            )
            if cat_urls:
                listing_queue = cat_urls + [u for u in listing_queue if u not in cat_urls]

        for listing in listing_queue:
            if collected >= target_count:
                break
            page.goto(listing, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3500)
            self._dismiss_popups(page)
            self._scroll_listing(page)
            # Prefer session flag — listing DOM often lacks logout link.
            if self.require_member_login and not (self.logged_in or self.is_logged_in()):
                raise LoginRequiredError(f"{self.site_code}: session lost while browsing listings.")
            if "/designers" in listing.rstrip("/").split("?")[0] and "/designer/" not in listing:
                continue
            for card in self._extract_listing_cards(page):
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
                if not detail or not self._is_valid_product_url(detail.source_url):
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
        urls: list[str] = []
        seen: set[str] = set()
        for index_url in DESIGNER_INDEX_URLS:
            page.goto(index_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            self._dismiss_popups(page)
            found = page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href*="/designer/"]'))
                  .map(a => (a.href || '').split('?')[0])
                  .filter(h => /\\/designer\\/[a-z0-9_]+$/i.test(h))"""
            )
            for href in found or []:
                clean = (href or "").split("?")[0].rstrip("/")
                # Prefer en-JP locale
                if "/en-JP/" not in clean and "/en-jp/" not in clean.lower():
                    clean = clean.replace("/it-IT/", "/en-JP/").replace("/it-it/", "/en-JP/")
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
        if any(x in lower for x in ["/customer/", "/checkout", "/cart", "/login", "/register", "wishlist"]):
            return False
        if "/designers" in lower or "/designer/" in lower:
            return False
        if re.search(r"/product/0(?:/|$)", lower):
            return False
        return bool(re.search(r"/product/[1-9]\d+/[^/]+/", lower))

    def _normalize_product_url(self, url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        path = parsed.path
        if "/it-IT/" in path or "/it-it/" in path:
            path = path.replace("/it-IT/", "/en-JP/").replace("/it-it/", "/en-JP/")
        return f"{parsed.scheme}://{parsed.netloc}{path}".rstrip("/")

    def _external_id_from_url(self, url: str) -> str:
        match = re.search(r"/product/(\d+)/", url)
        if match:
            return f"{self.site_code}:{match.group(1)}"
        path = urlparse(url).path.rstrip("/")
        tail = path.split("/")[-1]
        if tail:
            return f"{self.site_code}:{tail}"
        return f"{self.site_code}:{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}"

    def _extract_listing_cards(self, page: Page) -> list[dict]:
        return page.evaluate(
            """() => {
              const rows = [];
              const seen = new Set();
              for (const root of document.querySelectorAll('div.product')) {
                const a = root.querySelector('a[href*="/product/"]');
                if (!a || !a.href || !/\\/product\\/[1-9]\\d+\\//.test(a.href)) continue;
                const href = a.href.split('?')[0];
                if (seen.has(href)) continue;
                seen.add(href);
                let brand = '', name = '', price = root.getAttribute('data-origin-price') || '';
                try {
                  const row = root.getAttribute('data-gtm-row');
                  if (row) {
                    const j = JSON.parse(row);
                    brand = j.item_brand || '';
                    name = j.item_name || '';
                    if (!price && j.price) price = '¥ ' + j.price;
                  }
                } catch (e) {}
                const gtm = root.querySelector('[data-gtm-brand]');
                if (gtm) {
                  brand = brand || gtm.getAttribute('data-gtm-brand') || '';
                  name = name || gtm.getAttribute('data-gtm-name') || '';
                }
                if (!name) name = (root.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 160);
                const imgEl = root.querySelector('img');
                rows.push({
                  href, name: (name||'').trim(), brand: (brand||'').trim(),
                  price: (price||'').trim(),
                  img: imgEl ? (imgEl.currentSrc || imgEl.src || '') : ''
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
        page.wait_for_timeout(3500)
        self._dismiss_popups(page)
        try:
            page.wait_for_selector(".detail__price--new, .detail__subtitle, .js-product-price, .detail__header", timeout=20000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(800)
        if self.require_member_login and not (self.logged_in or self.is_logged_in()):
            raise LoginRequiredError(f"{self.site_code}: session lost on product detail.")

        product_id_match = re.search(r"/product/([1-9]\d+)/", href)
        product_id = product_id_match.group(1) if product_id_match else ""

        detail = page.evaluate(
            """(productId) => {
              const text = (sel) => {
                const el = document.querySelector(sel);
                return el ? (el.innerText || '').replace(/\\s+/g, ' ').trim() : '';
              };
              const brand = text('.detail__header') || text('h1.detail__header') || text('h1');
              const name = text('.detail__subtitle');
              const newPrice = text('.detail__price--new') || text('.js-product-price');
              const oldPrice = text('.detail__price--old');
              const priceBox = text('.detail__price');
              const discount = text('.detail__price--discount');
              const body = document.body ? (document.body.innerText || '') : '';
              let code = '';
              const m = body.match(/Code:\\s*([^\\n]+)/i);
              if (m) code = m[1].trim();
              let color = text('.detail__colors');
              if (!color) {
                const cm = body.match(/Color\\s+([^\\n]+)/i);
                if (cm) color = cm[1].trim();
              }
              let description = text('.detail__paragraph');
              const rawImgs = [];
              const pushUrl = (u) => {
                if (!u || typeof u !== 'string') return;
                const clean = u.trim().split(' ')[0].split('?')[0];
                if (!clean || clean.startsWith('data:')) return;
                if (!(clean.includes('montistorage') || clean.includes('/product/'))) return;
                if (clean.includes('flags/')) return;
                rawImgs.push(clean);
              };
              const fromSrcset = (raw) => {
                if (!raw) return;
                for (const part of String(raw).split(',')) {
                  const bits = part.trim().split(/\\s+/);
                  if (bits[0]) pushUrl(bits[0]);
                }
              };
              for (const img of Array.from(document.querySelectorAll('img'))) {
                fromSrcset(img.getAttribute('data-srcset') || '');
                fromSrcset(img.srcset || '');
                pushUrl(img.getAttribute('content') || '');
                pushUrl(img.getAttribute('data-src') || '');
                pushUrl(img.currentSrc || img.src || '');
              }
              const preferred = productId ? rawImgs.filter(u => u.includes('/product/' + productId + '/')) : rawImgs;
              const score = (u) => {
                const low = u.toLowerCase();
                if (low.includes('/mini/')) return 1;
                if (low.includes('/large/')) return 2;
                if (low.includes('/big/')) return 5;
                if (low.includes('/original/')) return 6;
                return 3;
              };
              const pool = (preferred.length ? preferred : rawImgs).filter(u => {
                const low = u.toLowerCase();
                return !low.includes('/mini/') && !low.includes('/micro/') && !low.includes('/medium/');
              });
              pool.sort((a, b) => score(b) - score(a));
              const uniq = [];
              for (const u of pool) if (!uniq.includes(u)) uniq.push(u);
              const originals = uniq.filter(u => u.toLowerCase().includes('/original/'));
              const finals = originals.length ? originals : uniq.filter(u => u.toLowerCase().includes('/big/'));
              return {
                brand, name, newPrice, oldPrice, priceBox, discount, code, color, description,
                imgs: (finals.length ? finals : uniq).slice(0, 12), href: location.href.split('?')[0]
              };
            }""",
            product_id,
        )

        brand = (detail.get("brand") or card.get("brand") or "").strip()
        name = (detail.get("name") or card.get("name") or "").strip()
        final_url = self._normalize_product_url(page.url or detail.get("href") or href)
        if not name or (brand and name.upper() == brand.upper()):
            name = self._product_name_from_url(final_url) or name or brand
        if not brand:
            brand = "Unknown Brand"
        if not self._is_valid_product_url(final_url) or not name:
            return None

        price_raw = (detail.get("newPrice") or detail.get("priceBox") or card.get("price") or "").strip()
        price_raw = price_raw.replace("\ufffd", "¥").replace("�", "¥")
        old_raw = (detail.get("oldPrice") or "").strip().replace("\ufffd", "¥").replace("�", "¥")
        amount, currency, price_text = parse_price_text(price_raw or "¥ 0")
        if currency == "JPY":
            price_text = f"¥ {int(amount):,}"
        ref_amount = None
        if old_raw:
            ref_amount, _, _ = parse_price_text(old_raw)

        product_code = (detail.get("code") or "").strip()
        color = (detail.get("color") or "").strip()
        description = (detail.get("description") or "").strip()
        images = detail.get("imgs") or []
        if not images and card.get("img"):
            images = [card["img"]]

        notes_parts = []
        if product_code:
            notes_parts.append(f"Code: {product_code}")
        if color:
            notes_parts.append(f"Color: {color}")
        sizes = extract_available_sizes(self.page)
        if sizes:
            notes_parts.append("Sizes: " + ",".join(sizes))
        if detail.get("discount"):
            notes_parts.append(f"Discount: {detail['discount']}")
        if description:
            notes_parts.append(description)

        stock = count_available_inventory(self.page, fallback=1 if amount > 0 else 0)
        if sizes and stock <= 0:
            stock = len(sizes)

        return ScrapedProduct(
            external_product_id=self._external_id_from_url(final_url),
            brand_name=brand,
            name=name,
            source_url=final_url,
            category=None,
            price=amount,
            reference_price=ref_amount,
            inventory=stock,
            popularity_rank=rank,
            model_line=product_code or None,
            image_urls=images or None,
            currency=currency,
            price_text=price_text,
            product_code=product_code or None,
            description=description or None,
            notes="\n".join(notes_parts) if notes_parts else None,
        )

    def _product_name_from_url(self, url: str) -> str:
        path = urlparse(url).path.rstrip("/")
        if "/product/" not in path:
            return ""
        slug = path.split("/")[-1]
        return slug.replace("_", " ").replace("-", " ").strip().title()
