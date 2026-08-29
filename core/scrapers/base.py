from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Iterable

from core.config import get_settings


@dataclass
class ScrapedProduct:
    external_product_id: str
    brand_name: str
    name: str
    source_url: str
    category: str | None
    price: float
    reference_price: float | None
    inventory: int
    popularity_rank: int
    model_line: str | None = None
    image_urls: list[str] | None = None
    currency: str = "EUR"
    price_text: str | None = None
    product_code: str | None = None
    description: str | None = None
    notes: str | None = None

    @property
    def first_image_url(self) -> str | None:
        if not self.image_urls:
            return None
        return self.image_urls[0]

    @property
    def display_price(self) -> str:
        if self.price_text:
            return self.price_text
        return f"€ {self.price:,.2f}"


class BaseScraper:
    site_code: str = ""
    site_name: str = ""
    base_url: str = ""

    def __init__(self) -> None:
        self.settings = get_settings()

    def jitter_wait(self) -> None:
        wait = random.uniform(self.settings.request_min_delay_seconds, self.settings.request_max_delay_seconds)
        time.sleep(wait)

    def login(self) -> None:
        raise NotImplementedError

    def scrape_latest(
        self,
        target_count: int,
        *,
        prefer_new: bool = True,
        skip_keys: set[str] | None = None,
        brand_filter: str | None = None,
        category_filter: str | None = None,
    ) -> Iterable[ScrapedProduct]:
        """Scrape products. prefer_new=True uses /new|/newin first; skip_keys dedupes vs CSV/workbook."""
        raise NotImplementedError
