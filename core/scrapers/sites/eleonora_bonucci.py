from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable
from urllib.parse import unquote, urlparse

from playwright.sync_api import Page

from core.scrapers.base import ScrapedProduct
from core.scrapers.dedupe import scraped_product_keys
from core.scrapers.playwright_base import LoginRequiredError, PlaywrightSiteScraper, count_available_inventory, parse_price_text

logger = logging.getLogger(__name__)

DESIGNER_INDEX_URLS = [
    "https://eleonorabonucci.com/en/women/designers",
    "https://eleonorabonucci.com/en/men/designers",
]

NEW_ARRIVAL_URLS = [
    "https://eleonorabonucci.com/en/women/newin",
    "https://eleonorabonucci.com/en/men/newin",
]

CATEGORY_FALLBACK_URLS = [
    *NEW_ARRIVAL_URLS,
    "https://eleonorabonucci.com/en/women/new-collection/clothing/dresses",
]

# PDP: /en/{brand}/women|men|kids/{dept}/{sub}/{id}
PRODUCT_URL_RE = re.compile(
    r"^https?://(?:www\.)?eleonorabonucci\.com/en/[^/]+/(?:women|men|kids)/[^/]+/[^/]+/\d+/?$",
    re.I,
)
BRAND_PLP_RE = re.compile(
    r"^https?://(?:www\.)?eleonorabonucci\.com/en/[^/]+/(?:women|men|kids)/?$",
    re.I,
)


class EleonoraBonucciScraper(PlaywrightSiteScraper):
    """Eleonora Bonucci — brand-first scrape.

    Designers → /en/{gender}/designers
    Brand PLP → /en/{brand_slug}/women (or men/kids)
    PDP → /en/{brand}/women/{dept}/{sub}/{numericId}
    """

    site_code = "eleonorabonucci"
    site_name = "Eleonora Bonucci"
    base_url = "https://eleonorabonucci.com/"
    profile_subdir = "eleonorabonucci"
    login_url = "https://eleonorabonucci.com/en/myaccount/login"
    require_member_login = True
    listing_urls = [*DESIGNER_INDEX_URLS, *CATEGORY_FALLBACK_URLS]

    def login(self) -> None:
        page = self.ensure_browser()
        account = self.account()
        for url in [
            self.login_url,
            "https://eleonorabonucci.com/en/login",
            "https://eleonorabonucci.com/en/myaccount/login",
        ]:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
            self._dismiss_popups(page)
            if page.locator("input[type='password']").count() > 0:
                break
        self._fill_login_form(page, account.email, account.password)
        page.wait_for_timeout(3500)
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

        collected = 0
        seen: set[str] = set()
        skip_keys = skip_keys or set()
        from core.scrapers.filters import card_matches_filters

        brand_urls: list[str] = []
        if prefer_new:
            listing_queue = list(NEW_ARRIVAL_URLS)
            logger.info("%s new-arrival queue: %s URLs", self.site_code, len(listing_queue))
        else:
            brand_urls = self._collect_brand_urls(page, limit=35)
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
            page.wait_for_timeout(4000)
            self._dismiss_popups(page)
            self._scroll_listing(page)
            if self.require_member_login and not (self.logged_in or self.is_logged_in()):
                raise LoginRequiredError(f"{self.site_code}: session lost while browsing listings.")
            if "/designers" in listing:
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

    def _collect_brand_urls(self, page: Page, limit: int = 35) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for index_url in DESIGNER_INDEX_URLS:
            page.goto(index_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3500)
            self._dismiss_popups(page)
            found = page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                  .map(a => (a.href || '').split('?')[0])
                  .filter(h => {
                    try {
                      const u = new URL(h);
                      if (!u.hostname.includes('eleonorabonucci')) return false;
                      const p = u.pathname.split('/').filter(Boolean);
                      // /en/{brand}/women|men|kids
                      return p.length === 3 && p[0]==='en' && ['women','men','kids'].includes(p[2])
                        && !['women','men','kids','sale','login','myaccount'].includes(p[1]);
                    } catch(e) { return false; }
                  })"""
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
        for _ in range(4):
            try:
                page.mouse.wheel(0, 1600)
                page.wait_for_timeout(800)
            except Exception:  # noqa: BLE001
                break

    def _is_valid_product_url(self, url: str) -> bool:
        if not url:
            return False
        lower = url.lower().split("?", 1)[0]
        if any(x in lower for x in ["/login", "/myaccount", "/cart", "/wishlist", "/shipment", "javascript:"]):
            return False
        if "/designers" in lower:
            return False
        return bool(PRODUCT_URL_RE.match(lower.rstrip("/")))

    def _normalize_product_url(self, url: str) -> str:
        if not url:
            return ""
        if url.startswith("/"):
            url = "https://eleonorabonucci.com" + url
        parsed = urlparse(unquote(url))
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    def _extract_listing_cards(self, page: Page) -> list[dict]:
        return page.evaluate(
            """() => {
              const rows = [];
              const seen = new Set();
              const roots = Array.from(document.querySelectorAll('.product.sf-dress, .product.clearfix, .iproduct, .product'));
              for (const root of roots) {
                // skip quick-view modal shell
                if ((root.className || '').includes('modal-padding')) continue;
                const a = root.querySelector('a[href*=\"/en/\"][href*=\"/women/\"], a[href*=\"/en/\"][href*=\"/men/\"], a[href*=\"/en/\"][href*=\"/kids/\"]');
                if (!a || !a.href) continue;
                const href = a.href.split('?')[0];
                if (!/\\/\\d+\\/?$/.test(href)) continue;
                if (seen.has(href)) continue;
                seen.add(href);
                const text = (root.innerText || '').replace(/\\s+/g, ' ').trim();
                const titleEl = root.querySelector('.product-title h3 a, .product-title a, .product-title h3, h3');
                const brandEl = root.querySelector('.product-title a[href$=\"/women\"], .product-title a[href$=\"/men\"], a.brand');
                let name = titleEl ? (titleEl.innerText || '').replace(/\\s+/g, ' ').trim() : '';
                let brand = brandEl ? (brandEl.innerText || '').replace(/\\s+/g, ' ').trim() : '';
                // Text often: BRAND\\nNAME\\n€ price
                if (!brand || !name) {
                  const lines = text.split(/(?=€)|\\n/).map(s => s.trim()).filter(Boolean);
                  // from card text like "FW26 Quick View MM6 MAISON MARGIELA CALZINI CON LOGO € 70.00"
                  const cleaned = text.replace(/FW\\d+/g, '').replace(/Quick View/gi, '').replace(/-\\d+%/g, '').trim();
                  const pm = cleaned.match(/€\\s*[\\d.,]+/);
                  const before = pm ? cleaned.slice(0, pm.index).trim() : cleaned;
                  const parts = before.split(/\\s{2,}|(?<=[A-Z])\\s(?=[A-Z][a-z])/);
                  if (!brand) {
                    // brand is often first multi-word uppercase chunk from URL
                    const m = href.match(/\\/en\\/([^/]+)\\//);
                    if (m) brand = decodeURIComponent(m[1]).replace(/[-_]/g, ' ').toUpperCase();
                  }
                  if (!name) {
                    name = before.replace(new RegExp('^' + brand.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&') + '\\s*', 'i'), '').trim() || before;
                  }
                }
                const priceMatch = text.match(/€\\s*[\\d.,]+/);
                const imgEl = root.querySelector('img');
                rows.push({
                  href,
                  name: (name || '').slice(0, 160),
                  brand: (brand || '').slice(0, 80),
                  price: priceMatch ? priceMatch[0] : '',
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
        # Close accidental quick-view / cart modal
        for sel in [".mfp-close", "a.mfp-close", "#Modal_PanelModalQuickView_Close"]:
            loc = page.locator(sel).first
            if loc.count() > 0:
                try:
                    loc.click(timeout=1500)
                    page.wait_for_timeout(500)
                except Exception:  # noqa: BLE001
                    pass

        if self.require_member_login and not (self.logged_in or self.is_logged_in()):
            raise LoginRequiredError(f"{self.site_code}: session lost on product detail.")

        product_id_match = re.search(r"/(\d+)/?$", href.rstrip("/"))
        product_id = product_id_match.group(1) if product_id_match else ""

        detail = page.evaluate(
            """(productId) => {
              const text = (sel) => {
                const el = document.querySelector(sel);
                return el ? (el.innerText || '').replace(/\\s+/g, ' ').trim() : '';
              };
              const body = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
              const title = document.title || '';
              // Title: "BRAND - NAME - Eleonora Bonucci"
              let brand = '', name = '';
              const tm = title.match(/^(.+?)\\s+-\\s+(.+?)\\s+-\\s+Eleonora/i);
              if (tm) { brand = tm[1].trim(); name = tm[2].trim(); }
              const price = text('.product-price') || text('.product-desc .product-price');
              let styleId = '';
              const sm = body.match(/Style ID\\s+([A-Za-z0-9_\\-]+)/i);
              if (sm) styleId = sm[1];
              // Body fallback near FW26 BRAND NAME €
              if (!name || !brand) {
                const m = body.match(/(?:FW\\d+\\s+)?([A-Z0-9][A-Z0-9 &.\\-']{1,40})\\s+([A-ZÀ-Ü][^€]{3,80}?)\\s+€\\s*[\\d.,]+/);
                if (m) {
                  brand = brand || m[1].trim();
                  name = name || m[2].trim();
                }
              }
              const imgs = Array.from(document.querySelectorAll('img'))
                .map(i => i.currentSrc || i.src || '')
                .filter(u => u && u.includes('images.eleonorabonucci.com/photo/'))
                .filter(u => !productId || u.includes('/photo/' + productId + '/'));
              const uniq = [];
              for (const u of imgs) if (!uniq.includes(u)) uniq.push(u);
              return {
                brand, name, price, styleId,
                imgs: uniq.slice(0, 20),
                href: location.href.split('?')[0],
                title
              };
            }""",
            product_id,
        )

        brand = (detail.get("brand") or card.get("brand") or "").strip()
        name = (detail.get("name") or card.get("name") or "").strip()
        # Drop modal noise
        if name and "shopping bag" in name.lower():
            name = (card.get("name") or "").strip()
        if not brand:
            m = re.search(r"/en/([^/]+)/", href)
            if m:
                brand = unquote(m.group(1)).replace("-", " ").replace("_", " ").title()
        if not name or not self._is_valid_product_url(self._normalize_product_url(detail.get("href") or href)):
            return None

        price_raw = (detail.get("price") or card.get("price") or "").strip()
        amount, currency, price_text = parse_price_text(price_raw or "€ 0")
        images = detail.get("imgs") or []
        if not images and card.get("img"):
            images = [card["img"]]
        final_url = self._normalize_product_url(page.url or detail.get("href") or href)
        style_id = (detail.get("styleId") or "").strip()

        return ScrapedProduct(
            external_product_id=f"{self.site_code}:{product_id or hashlib.sha1(final_url.encode()).hexdigest()[:12]}",
            brand_name=brand or "Unknown Brand",
            name=name,
            source_url=final_url,
            category=None,
            price=amount,
            reference_price=None,
            inventory=count_available_inventory(self.page, fallback=1 if amount > 0 else 0),
            popularity_rank=rank,
            model_line=style_id or None,
            image_urls=images or None,
            currency=currency,
            price_text=price_text,
            product_code=style_id or None,
        )
