"""Project paths and settings (no database)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.paths import resolve_resource_path, runtime_root, seed_runtime_notice_assets

ROOT_DIR = runtime_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="EC-Buyma Engines", alias="APP_NAME")

    secrets_dir: Path = Field(default=Path("./secrets"), alias="SECRETS_DIR")
    workspace_dir: Path = Field(default=Path("./workspace"), alias="WORKSPACE_DIR")
    assets_dir: Path = Field(default=Path("./assets"), alias="ASSETS_DIR")
    log_dir: Path = Field(default=Path("./logs"), alias="LOG_DIR")

    request_min_delay_seconds: float = Field(default=1.5, alias="REQUEST_MIN_DELAY_SECONDS")
    request_max_delay_seconds: float = Field(default=4.0, alias="REQUEST_MAX_DELAY_SECONDS")
    max_retries: int = Field(default=3, alias="MAX_RETRIES")

    chatgpt_profile_path: Path = Field(
        default=Path("C:/chrome-profiles/chatgpt-worker"),
        alias="CHATGPT_PROFILE_PATH",
    )
    chatgpt_description_project_url: str = Field(
        default="https://chatgpt.com/g/g-p-6a736100d6dc819199dbb0ebe2c01e98-buyma/c/6a736154-6760-83ee-87c2-56626cd67c33",
        alias="CHATGPT_DESCRIPTION_PROJECT_URL",
    )
    chatgpt_image_project_url: str = Field(
        default="https://chatgpt.com/g/g-p-6a736100d6dc819199dbb0ebe2c01e98-buyma/c/6a7361cb-03e4-83ee-a471-64a6721bea96",
        alias="CHATGPT_IMAGE_PROJECT_URL",
    )
    chatgpt_login_timeout_seconds: int = Field(default=300, alias="CHATGPT_LOGIN_TIMEOUT_SECONDS")
    chatgpt_wait_seconds: int = Field(default=90, alias="CHATGPT_WAIT_SECONDS")
    chatgpt_auto_send: bool = Field(default=True, alias="CHATGPT_AUTO_SEND")
    chatgpt_cookies_path: Path = Field(
        default=Path("./secrets/chatgpt_cookies.json"),
        alias="CHATGPT_COOKIES_PATH",
    )
    chatgpt_pause_hours: int = Field(default=3, alias="CHATGPT_PAUSE_HOURS")
    # http = cookie backend (no Chrome); browser = Playwright UI
    chatgpt_transport: str = Field(default="http", alias="CHATGPT_TRANSPORT")

    provided_image_1: Path = Field(default=Path("./assets/provided_image_1.png"), alias="PROVIDED_IMAGE_1")
    provided_image_2: Path = Field(default=Path("./assets/provided_image_2.png"), alias="PROVIDED_IMAGE_2")
    brand_intro_image: Path = Field(default=Path("./assets/brand_intro_image.png"), alias="BRAND_INTRO_IMAGE")

    ec_site_email: str = Field(default="", alias="EC_SITE_EMAIL")
    ec_site_password: str = Field(default="", alias="EC_SITE_PASSWORD")
    ec_minetti_password: str = Field(default="", alias="EC_MINETTI_PASSWORD")
    ec_julian_email: str = Field(default="", alias="EC_JULIAN_EMAIL")
    ec_julian_password: str = Field(default="", alias="EC_JULIAN_PASSWORD")
    ec_monti_email: str = Field(default="", alias="EC_MONTI_EMAIL")
    ec_monti_password: str = Field(default="", alias="EC_MONTI_PASSWORD")
    ec_minetti_email: str = Field(default="", alias="EC_MINETTI_EMAIL")
    ec_eleonora_email: str = Field(default="", alias="EC_ELEONORA_EMAIL")
    ec_eleonora_password: str = Field(default="", alias="EC_ELEONORA_PASSWORD")
    buyma_account_email: str = Field(default="", alias="BUYMA_ACCOUNT_EMAIL")
    buyma_account_password: str = Field(default="", alias="BUYMA_ACCOUNT_PASSWORD")

    eur_to_jpy_rate: float = Field(default=185.68, alias="EUR_TO_JPY_RATE")
    usd_to_jpy_rate: float = Field(default=150.0, alias="USD_TO_JPY_RATE")
    gbp_to_jpy_rate: float = Field(default=190.0, alias="GBP_TO_JPY_RATE")
    buyma_procurement_area: str = Field(default="海外:ヨーロッパ:イタリア:選択なし", alias="BUYMA_PROCUREMENT_AREA")
    buyma_ship_from: str = Field(default="国内:愛知県::", alias="BUYMA_SHIP_FROM")
    buyma_shipping_method: str = Field(default="ヤマト運輸 - 宅急便", alias="BUYMA_SHIPPING_METHOD")
    buyma_duty_burden: str = Field(default="出品者負担(税込)", alias="BUYMA_DUTY_BURDEN")
    buyma_overseas_shipping_eur: float = Field(default=50.0, alias="BUYMA_OVERSEAS_SHIPPING_EUR")
    buyma_domestic_shipping_jpy: int = Field(default=1200, alias="BUYMA_DOMESTIC_SHIPPING_JPY")
    buyma_packaging_jpy: int = Field(default=0, alias="BUYMA_PACKAGING_JPY")
    buyma_profit_rate: float = Field(default=0.03, alias="BUYMA_PROFIT_RATE")
    buyma_min_profit_jpy: int = Field(default=3000, alias="BUYMA_MIN_PROFIT_JPY")
    buyma_fee_keep_rate: float = Field(default=0.923, alias="BUYMA_FEE_KEEP_RATE")
    buyma_taxable_ratio: float = Field(default=0.6, alias="BUYMA_TAXABLE_RATIO")
    # Japanese low-value import: no duty/tax when 課税価格 is at or below this.
    buyma_duty_free_taxable_jpy: int = Field(default=16666, alias="BUYMA_DUTY_FREE_TAXABLE_JPY")
    scrape_default_count: int = Field(default=20, alias="SCRAPE_DEFAULT_COUNT")
    stock_monitor_interval_minutes: int = Field(default=60, alias="STOCK_MONITOR_INTERVAL_MINUTES")
    scrape_output_dir: Path = Field(default=Path("./workspace/scrape"), alias="SCRAPE_OUTPUT_DIR")
    generate_output_dir: Path = Field(default=Path("./workspace/generate"), alias="GENERATE_OUTPUT_DIR")

    buyma_profile_path: Path = Field(
        default=Path("C:/chrome-profiles/buyma-worker"),
        alias="BUYMA_PROFILE_PATH",
    )
    buyma_cookies_path: Path = Field(
        default=Path("./secrets/buyma_cookies.json"),
        alias="BUYMA_COOKIES_PATH",
    )
    buyma_new_listing_url: str = Field(
        default="https://www.buyma.com/my/sell/new?tab=b",
        alias="BUYMA_NEW_LISTING_URL",
    )
    buyma_login_timeout_seconds: int = Field(default=180, alias="BUYMA_LOGIN_TIMEOUT_SECONDS")
    buyma_min_delay_seconds: float = Field(default=1.5, alias="BUYMA_MIN_DELAY_SECONDS")
    buyma_max_delay_seconds: float = Field(default=3.5, alias="BUYMA_MAX_DELAY_SECONDS")
    buyma_select_delay_min_seconds: float = Field(default=1.2, alias="BUYMA_SELECT_DELAY_MIN_SECONDS")
    buyma_select_delay_max_seconds: float = Field(default=2.8, alias="BUYMA_SELECT_DELAY_MAX_SECONDS")
    buyma_between_items_min_seconds: float = Field(default=12.0, alias="BUYMA_BETWEEN_ITEMS_MIN_SECONDS")
    buyma_between_items_max_seconds: float = Field(default=25.0, alias="BUYMA_BETWEEN_ITEMS_MAX_SECONDS")
    buyma_failed_retry_passes: int = Field(default=0, alias="BUYMA_FAILED_RETRY_PASSES")
    buyma_max_images: int = Field(default=10, alias="BUYMA_MAX_IMAGES")
    buyma_auto_submit: bool = Field(default=True, alias="BUYMA_AUTO_SUBMIT")

    # One Excel file; each Engine1/2 batch appends a new sheet (tab).
    products_workbook_path: Path = Field(
        default=Path("./workspace/generate/products_workbook.xlsx"),
        alias="PRODUCTS_WORKBOOK_PATH",
    )

    google_sheets_enabled: bool = Field(default=False, alias="GOOGLE_SHEETS_ENABLED")
    google_sheets_spreadsheet_id: str = Field(default="", alias="GOOGLE_SHEETS_SPREADSHEET_ID")
    google_sheets_worksheet: str = Field(default="products", alias="GOOGLE_SHEETS_WORKSHEET")
    google_service_account_json: Path = Field(
        default=Path("./secrets/google_service_account.json"),
        alias="GOOGLE_SERVICE_ACCOUNT_JSON",
    )

    # Compat aliases used by ported listing/chatgpt code
    @property
    def production_root(self) -> Path:
        return self.workspace_dir / "generate"

    @property
    def export_root(self) -> Path:
        return self.workspace_dir / "buyma"


def clear_settings_cache() -> None:
    get_settings.cache_clear()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Ensure relative paths resolve next to the .exe / repo root.
    try:
        os.chdir(ROOT_DIR)
    except Exception:  # noqa: BLE001
        pass
    settings = Settings()
    seed_runtime_notice_assets()
    for attr in (
        "secrets_dir",
        "workspace_dir",
        "assets_dir",
        "log_dir",
        "chatgpt_cookies_path",
        "buyma_cookies_path",
        "provided_image_1",
        "provided_image_2",
        "brand_intro_image",
        "google_service_account_json",
        "products_workbook_path",
        "scrape_output_dir",
        "generate_output_dir",
    ):
        value = getattr(settings, attr)
        if not isinstance(value, Path):
            continue
        if attr in {"provided_image_1", "provided_image_2", "brand_intro_image"}:
            setattr(settings, attr, resolve_resource_path(value))
        elif not value.is_absolute():
            setattr(settings, attr, (ROOT_DIR / value).resolve())
    settings.secrets_dir.mkdir(parents=True, exist_ok=True)
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    (settings.workspace_dir / "scrape").mkdir(parents=True, exist_ok=True)
    (settings.workspace_dir / "generate").mkdir(parents=True, exist_ok=True)
    (settings.workspace_dir / "buyma").mkdir(parents=True, exist_ok=True)
    settings.scrape_output_dir.mkdir(parents=True, exist_ok=True)
    settings.generate_output_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    settings.chatgpt_cookies_path.parent.mkdir(parents=True, exist_ok=True)
    settings.buyma_cookies_path.parent.mkdir(parents=True, exist_ok=True)
    return settings
