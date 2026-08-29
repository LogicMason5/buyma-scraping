"""Quick regression checks for pricing / filters / monitor helpers."""

from __future__ import annotations

from pathlib import Path

from core.buyma.buyma_listing_service import BuymaListingInput, calculate_buyma_pricing, reprice_row_from_cost
from core.config import clear_settings_cache, get_settings
from core.csv_schema import write_products_csv
from core.monitor.stock_monitor import run_stock_monitor
from core.scrapers.filters import card_matches_filters


def main() -> None:
    clear_settings_cache()
    s = get_settings()
    assert abs(s.eur_to_jpy_rate - 185.68) < 0.001, s.eur_to_jpy_rate
    overseas = round(s.buyma_overseas_shipping_eur * s.eur_to_jpy_rate)
    assert overseas == 9284, overseas

    p = calculate_buyma_pricing(
        BuymaListingInput(
            brand_name="Test",
            product_name="Bag",
            source_url="https://example.com/p/1",
            shop_name="Shop",
            price=100.0,
            currency="EUR",
        ),
        profit_rate=0.05,
    )
    assert p.overseas_jpy == 9284
    assert p.taxable == int(p.product_jpy * 0.6)
    assert p.duty == 0 and p.consumption_tax == 0  # 課税 ≤ ¥16,666
    assert p.profit == 3000
    assert abs(p.buyma_fee / p.listing_price - 0.077) < 0.01

    p3 = calculate_buyma_pricing(
        BuymaListingInput(
            brand_name="Test",
            product_name="Bag",
            source_url="https://example.com/p/1",
            shop_name="Shop",
            price=100.0,
            currency="EUR",
        ),
        profit_rate=3,  # percent style
    )
    assert abs(p3.profit_rate - 0.03) < 1e-9

    assert card_matches_filters({"brand": "Gucci", "href": "/bags/x", "name": "n"}, brand_filter="gucci")
    assert not card_matches_filters({"brand": "Prada", "href": "/bags/x", "name": "n"}, brand_filter="gucci")
    assert card_matches_filters({"brand": "X", "href": "/women/shoes/1", "name": "n"}, category_filter="shoes")

    row = reprice_row_from_cost(
        {
            "品代(海外仕入)": str(p.product_jpy),
            "通貨": "EUR",
            "ブランド": "Test",
            "商品名": "【Test】Bag 関税無＆送料無料",
            "仕入先URL": "https://example.com/p/1",
            "在庫": "1",
        },
        profit_rate=0.05,
    )
    assert row["送料(仕入)"] == "9284"
    assert row["出品価格"] == str(p.listing_price)

    # Cheap EUR item (品代 JPY <= 500) must not explode.
    cheap = reprice_row_from_cost(
        {
            "品代(海外仕入)": "371",
            "通貨": "EUR",
            "ブランド": "T",
            "商品名": "Cheap",
            "仕入先URL": "https://example.com/p/2",
            "在庫": "1",
        },
        profit_rate=0.05,
    )
    assert int(cheap["出品価格"]) < 50000, cheap["出品価格"]

    # Optional live monitor smoke (skipped if no network / login). Use MonitorResult API.
    tmp = Path("workspace/tmp_monitor_test.csv")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    write_products_csv(
        tmp,
        [
            {
                "商品名": "Gone Item",
                "ブランド": "T",
                "仕入先URL": "https://www.julian-fashion.com/en-JP/product/0/missing/missing/gone",
                "フォルダ名": "T_Gone",
                "在庫": "1",
                "サイトコード": "julian-fashion",
            }
        ],
    )
    try:
        result = run_stock_monitor(csv_path=tmp, log=print, refresh_on_recovery=False)
        print("monitor hits", len(result.hits), "recovered", len(result.recovered))
    except Exception as exc:  # noqa: BLE001
        print("monitor smoke skipped:", exc)
    print("OK")


if __name__ == "__main__":
    main()
