from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable
from urllib.parse import urlparse

from playwright.sync_api import Page

from core.scrapers.base import ScrapedProduct
from core.scrapers.dedupe import scraped_product_keys
from core.scrapers.playwright_base import PlaywrightSiteScraper, count_available_inventory, parse_price_text

logger = logging.getLogger(__name__)

# Old minettiangeloonline.com is a dead register stub; live shop is angelominetti.it (PrestaShop).
BRAND_INDEX_URL = "https://www.angelominetti.it/brands"
# Prefer "new" / new-season category pages for daily arrivals.
NEW_ARRIVAL_URLS = [
    "https://www.angelominetti.it/woman/clothing/new/",
    "https://www.angelominetti.it/woman/",
    "https://www.angelominetti.it/man/",
]

CATEGORY_FALLBACK_URLS = [
    *NEW_ARRIVAL_URLS,
    "https://www.angelominetti.it/woman/clothing/dresses/",
]

PRODUCT_URL_RE = re.compile(
    r"^https?://(?:www\.)?angelominetti\.it/[a-z0-9\-]+/\d+-\d+-[a-z0-9\-]+\.html",
    re.I,
)


class MinettiAngeloScraper(PlaywrightSiteScraper):
    """Angelo Minetti (angelominetti.it) — brand-first PrestaShop scrape.

    designers → /brands
    brand PLP → /brand/{slug} (or manufacturer URLs under /brands)
    PDP → /{category}/{productId}-{attrId}-{slug}.html
    """

    site_code = "minettiangeloonline"
    site_name = "Minetti Angelo"
    base_url = "https://www.angelominetti.it/"
    profile_subdir = "minettiangeloonline"
    login_url = "https://www.angelominetti.it/login"
    # Public EUR prices are shown; login attempted but not required (creds may be for old domain).
    require_member_login = False
    listing_urls = [BRAND_INDEX_URL, *CATEGORY_FALLBACK_URLS]

    def login(self) -> None:
        page = self.ensure_browser()
        account = self.account()
        page.goto(self.login_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        self._dismiss_popups(page)

        email = page.locator("input[type='email'][name='email']:visible, form input[name='email']:visible").first
        password = page.locator("input[type='password'][name='password']:visible").first
        if email.count() == 0 or password.count() == 0:
            logger.warning("%s: login form not found; continuing as guest", self.site_code)
            return
        email.fill(account.email)
        password.fill(account.password)
        page.wait_for_timeout(400)
        # Prefer the account SIGN IN submit (avoid search-btn).
        btn = page.locator(
            "button#submit-login:visible, "
            "form#login-form button[type='submit']:visible, "
            "button[data-link-action='sign-in']:visible, "
            "button:has-text('SIGN IN'):visible, "
            "button:has-text('Sign in'):visible"
        ).first
        if btn.count() > 0:
            try:
                btn.click(timeout=8000)
            except Exception:  # noqa: BLE001
                password.press("Enter")
        else:
            password.press("Enter")
        page.wait_for_timeout(3500)
        self._dismiss_popups(page)
        body = ""
        try:
            body = page.inner_text("body")
        except Exception:  # noqa: BLE001
            pass
        if re.search(r"authentication failed|login failed|invalid", body, re.I):
            logger.warning(
                "%s: login rejected for %s (old domain creds?). Scraping guest prices.",
                self.site_code,
                account.email,
            )
            self.logged_in = False
        else:
            self.logged_in = self.is_logged_in()

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
        try:
            self.login()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s login skipped: %s", self.site_code, exc)

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
            logger.info("%s listing queue: %s brand/category pages", self.site_code, len(listing_queue))
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
            if listing.rstrip("/").endswith("/brands"):
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
        page.goto(BRAND_INDEX_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3500)
        self._dismiss_popups(page)
        found = page.evaluate(
            """() => {
              const out = [];
              for (const a of document.querySelectorAll('a[href]')) {
                const href = (a.href || '').split('?')[0].split('#')[0];
                const text = (a.innerText || '').replace(/\\s+/g, ' ').trim();
                if (!href.includes('angelominetti.it')) continue;
                if (/\\/brand\\//i.test(href)) out.push(href);
                else if (/id_manufacturer=/i.test(a.href)) out.push(a.href.split('#')[0]);
                // Brands index letter groups link to manufacturer pages like /brand/12-alexander-wang
                else if (text.length > 1 && text.length < 40
                  && /\\/brand\\/?/i.test(href) === false
                  && /\\/[a-z0-9\\-]+-\\d+/.test(href) === false
                  && href.includes('/brand')) out.push(href);
              }
              return [...new Set(out)];
            }"""
        )
        urls: list[str] = []
        seen: set[str] = set()
        for href in found or []:
            clean = (href or "").split("#")[0]
            if not clean or clean in seen:
                continue
            seen.add(clean)
            urls.append(clean)
            if len(urls) >= limit:
                break
        # Fallback: category pages still carry brand on each card.
        if len(urls) < 3:
            logger.info("%s: few brand URLs (%s); using category fallbacks", self.site_code, len(urls))
            urls = list(CATEGORY_FALLBACK_URLS)
        return urls

    def _scroll_listing(self, page: Page) -> None:
        for _ in range(3):
            try:
                page.mouse.wheel(0, 1600)
                page.wait_for_timeout(700)
            except Exception:  # noqa: BLE001
                break

    def _is_valid_product_url(self, url: str) -> bool:
        if not url:
            return False
        lower = url.lower().split("#", 1)[0].split("?", 1)[0]
        if any(x in lower for x in ["/login", "/cart", "/order", "/password", "/content/", "/brands"]):
            return False
        return bool(PRODUCT_URL_RE.match(lower))

    def _normalize_product_url(self, url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url.split("#")[0])
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    def _extract_listing_cards(self, page: Page) -> list[dict]:
        return page.evaluate(
            """() => {
              const rows = [];
              const seen = new Set();
              for (const root of document.querySelectorAll('article.product-miniature, .js-product-miniature')) {
                const a = root.querySelector('a.product-thumbnail, a.thumbnail, a[href*=".html"]');
                if (!a || !a.href || !/\\/\\d+-\\d+-[^/]+\\.html/i.test(a.href)) continue;
                const href = a.href.split('#')[0].split('?')[0];
                if (seen.has(href)) continue;
                seen.add(href);
                const brand = ((root.querySelector('.product-brand, .manu-name') || {}).innerText || '').replace(/\\s+/g,' ').trim();
                const name = ((root.querySelector('h3.product-title, .product-title a, .product-title') || {}).innerText || '').replace(/\\s+/g,' ').trim();
                const price = ((root.querySelector('.product-price, .price') || {}).innerText || '').replace(/\\s+/g,' ').trim();
                const imgEl = root.querySelector('img.product-thumbnail-first, img');
                let fallbackBrand = brand;
                let fallbackName = name;
                if (!fallbackBrand || !fallbackName) {
                  const text = (root.innerText || '').replace(/\\s+/g, ' ').trim()
                    .replace(/NEW SEASON/gi, '').replace(/SIZE.*/i, '').trim();
                  const pm = text.match(/€\\s*[\\d.,]+/);
                  const before = pm ? text.slice(0, pm.index).trim() : text;
                  if (!fallbackName) fallbackName = before;
                }
                rows.push({
                  href,
                  name: fallbackName.slice(0, 160),
                  brand: fallbackBrand.slice(0, 80),
                  price,
                  img: imgEl ? (imgEl.getAttribute('data-full-size-image-url') || imgEl.currentSrc || imgEl.src || '') : '',
                  productId: root.getAttribute('data-id-product') || ''
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
        page.wait_for_timeout(3000)
        self._dismiss_popups(page)

        detail = page.evaluate(
            """() => {
              const text = (sel) => {
                const el = document.querySelector(sel);
                return el ? (el.innerText || '').replace(/\\s+/g, ' ').trim() : '';
              };
              const brand = text('.product-manufacturer a, .product-brand, .manufacturer span, [itemprop="brand"]')
                || text('.product-manufacturer');
              const name = text('h1.h1, h1[itemprop="name"], h1');
              const price = text('.current-price .price, .current-price, .product-price, [itemprop="price"]');
              const oldPrice = text('.regular-price');
              const imgs = Array.from(document.querySelectorAll(
                '.product-cover img, .js-qv-product-cover, .product-images img, img[data-full-size-image-url], img.js-thumb'
              ))
                .map(img => img.getAttribute('data-image-large-src')
                  || img.getAttribute('data-full-size-image-url')
                  || img.currentSrc || img.src || '')
                .filter(u => u && !u.startsWith('data:') && !u.includes('logo'));
              const uniq = [];
              for (const u of imgs) if (!uniq.includes(u)) uniq.push(u);
              return {
                brand, name, price, oldPrice,
                imgs: uniq.slice(0, 20),
                href: location.href.split('#')[0].split('?')[0]
              };
            }"""
        )

        brand = (detail.get("brand") or card.get("brand") or "").strip()
        name = (detail.get("name") or card.get("name") or "").strip()
        # Card text often "BRAND name..." — split if brand empty
        if not brand and card.get("name"):
            # From listing text pattern already separated when possible
            pass
        if not brand:
            # Try first line of title-like card text
            text = (card.get("name") or "")
            # Prefer manufacturer from URL path? unavailable — leave Unknown
            brand = "Unknown Brand"
        # If name still contains brand prefix, strip it
        if brand and brand != "Unknown Brand" and name.upper().startswith(brand.upper()):
            name = name[len(brand) :].strip(" -")

        final_url = self._normalize_product_url(page.url or detail.get("href") or href)
        if not name or not self._is_valid_product_url(final_url):
            return None
        if any(x in name.lower() for x in ["authentication failed", "page not found", "not found"]):
            return None

        price_raw = (detail.get("price") or card.get("price") or "").strip().replace("\ufffd", "€").replace("�", "€")
        amount, currency, price_text = parse_price_text(price_raw or "€ 0")
        ref_amount = None
        old_raw = (detail.get("oldPrice") or "").strip()
        if old_raw:
            ref_amount, _, _ = parse_price_text(old_raw)

        images = detail.get("imgs") or []
        if not images and card.get("img"):
            images = [card["img"]]

        pid = card.get("productId") or ""
        m = re.search(r"/(\d+)-\d+-", final_url)
        if m:
            pid = pid or m.group(1)

        return ScrapedProduct(
            external_product_id=f"{self.site_code}:{pid or hashlib.sha1(final_url.encode()).hexdigest()[:12]}",
            brand_name=brand,
            name=name,
            source_url=final_url,
            category=None,
            price=amount,
            reference_price=ref_amount,
            inventory=count_available_inventory(self.page, fallback=1 if amount > 0 else 0),
            popularity_rank=rank,
            image_urls=images or None,
            currency=currency,
            price_text=price_text,
        )
