"""Final regression suite: pricing (#6/#17), reprice, workbook, monitor helpers, filters."""

from __future__ import annotations

import tempfile
from pathlib import Path

from openpyxl import Workbook

from core.buyma.buyma_listing_service import (
    BuymaListingInput,
    build_buyma_row,
    calculate_buyma_pricing,
    reprice_row_from_cost,
    source_amount_from_product_jpy,
)
from core.config import clear_settings_cache, get_settings
from core.csv_schema import ensure_row, write_products_csv
from core.monitor.product_refresh import merge_refreshed_row
from core.monitor.stock_monitor import MonitorResult, evaluate_product_availability, load_monitor_rows
from core.scrapers.filters import card_matches_filters
from core.workbook.products_workbook import patch_workbook_fields, read_all_workbook_rows


class _FakePage:
    def __init__(self, *, status: int = 200, body: str = "Add to cart", title: str = "Product", stock: int = 2):
        self._status = status
        self._body = body
        self._title = title
        self._stock = stock

    def title(self) -> str:
        return self._title

    def inner_text(self, _sel: str = "body") -> str:
        return self._body

    def evaluate(self, script, *args):  # noqa: ANN001
        src = str(script)
        if "performance" in src:
            return self._status
        if "add to" in src.lower() or "add-to-cart" in src or "Aggiungi" in src:
            return "add to cart" in self._body.lower() or "add to bag" in self._body.lower()
        return self._body.lower()


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_pricing_formula() -> None:
    clear_settings_cache()
    s = get_settings()
    _assert(abs(s.eur_to_jpy_rate - 185.68) < 0.001, f"EUR rate {s.eur_to_jpy_rate}")
    _assert(int(50 * s.eur_to_jpy_rate) == 9284, "overseas JPY")
    _assert(s.buyma_duty_free_taxable_jpy == 16666, "duty-free threshold")
    _assert(s.buyma_min_profit_jpy == 3000, "min profit")

    # EUR100 is below 課税¥16,666 → duty/tax exempt, profit floored at ¥3,000
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
    product = int(100 * 185.68)
    taxable = int(product * 0.6)
    _assert(p.product_jpy == product, f"product {p.product_jpy}")
    _assert(p.overseas_jpy == 9284, f"overseas {p.overseas_jpy}")
    _assert(taxable <= 16666, "EUR100 should be duty-free")
    _assert(p.duty == 0 and p.consumption_tax == 0, f"exempt duty/tax {p.duty}/{p.consumption_tax}")
    _assert(p.profit == 3000, f"min profit {p.profit}")
    _assert("免税" in p.calc_text, "exempt label")

    p3 = calculate_buyma_pricing(
        BuymaListingInput(
            brand_name="Test",
            product_name="Bag",
            source_url="https://example.com/p/1",
            shop_name="Shop",
            price=100.0,
            currency="EUR",
        ),
        profit_rate=3,
    )
    _assert(abs(p3.profit_rate - 0.03) < 1e-9, "percent-style profit")

    z = calculate_buyma_pricing(
        BuymaListingInput(
            brand_name="T",
            product_name="B",
            source_url="u",
            shop_name="S",
            price=0,
            currency="EUR",
        ),
        profit_rate=0.03,
    )
    _assert(z.listing_price > 0, "zero product still has shipping costs")


def test_sheet_examples() -> None:
    """Match the customer's confirmed Monti / Julian sheet rules."""
    clear_settings_cache()

    # Trickers: duty applies, profit floored at 3000
    t = calculate_buyma_pricing(
        BuymaListingInput(
            brand_name="Trickers",
            product_name="Chelsea",
            source_url="https://www.montiboutique.com/en-JP/product/47906/trickers/x/y",
            shop_name="Monti",
            price=370.49,
            currency="EUR",
            product_code="2754/3 BLACK",
        ),
        profit_rate=0.03,
    )
    _assert(t.product_jpy == 68792, f"trickers 品代 {t.product_jpy}")
    _assert(t.taxable == 41275, f"trickers taxable {t.taxable}")
    _assert(t.duty == 4127 and t.consumption_tax == 4540, f"trickers tax {t.duty}/{t.consumption_tax}")
    _assert(t.cost_total == 87943, f"trickers cost {t.cost_total}")
    _assert(t.profit == 3000, f"trickers profit {t.profit}")
    _assert(t.listing_price == 98600, f"trickers sell {t.listing_price}")
    _assert("関税10%" in t.calc_text, "duty label")

    # Balenciaga: duty applies, 3% > 3000
    b = calculate_buyma_pricing(
        BuymaListingInput(
            brand_name="Balenciaga",
            product_name="Track",
            source_url="https://www.montiboutique.com/en-JP/product/58194/x/y/z",
            shop_name="Monti",
            price=606.56,
            currency="EUR",
            product_code="542023/W1GB1 1000 BLACK",
        ),
        profit_rate=0.03,
    )
    _assert(b.product_jpy in {112625, 112626}, f"bal 品代 {b.product_jpy}")
    _assert(b.duty > 0 and b.consumption_tax > 0, "bal duty applies")
    _assert(b.profit >= 4118, f"bal profit {b.profit}")
    _assert(b.listing_price == 153300, f"bal sell {b.listing_price}")

    # Ferragamo: duty applies, profit floored
    f = calculate_buyma_pricing(
        BuymaListingInput(
            brand_name="Ferragamo",
            product_name="Gancini",
            source_url="https://www.montiboutique.com/en-JP/product/63269/x/y/z",
            shop_name="Monti",
            price=334.43,
            currency="EUR",
            product_code="029392/0642848 NERO",
        ),
        profit_rate=0.03,
    )
    _assert(f.product_jpy == 62096, f"fer 品代 {f.product_jpy}")
    _assert(f.duty == 3725 and f.consumption_tax == 4098, f"fer tax {f.duty}/{f.consumption_tax}")
    _assert(f.cost_total == 80403, f"fer cost {f.cost_total}")
    _assert(f.profit == 3000, f"fer profit {f.profit}")
    _assert(f.listing_price == 90400, f"fer sell {f.listing_price}")

    # Converse cheap: duty-free
    c = calculate_buyma_pricing(
        BuymaListingInput(
            brand_name="Converse",
            product_name="Chuck",
            source_url="https://www.montiboutique.com/en-JP/product/43059/x/y/z",
            shop_name="Monti",
            price=61.47,
            currency="EUR",
            product_code="P1K122 GREY",
        ),
        profit_rate=0.03,
    )
    _assert(c.duty == 0 and c.consumption_tax == 0, "converse exempt")
    _assert(c.profit == 3000, f"converse profit {c.profit}")
    _assert(c.listing_price == 27000, f"converse sell {c.listing_price}")
    _assert("免税" in c.calc_text, "converse exempt label")

    # SKU must stay product code, never the formula dump / color-only
    row = build_buyma_row(
        BuymaListingInput(
            brand_name="Trickers",
            product_name="Chelsea",
            source_url="https://www.montiboutique.com/en-JP/product/47906/x/y/z",
            shop_name="Monti",
            price=370.49,
            currency="EUR",
            product_code="2754/3 BLACK",
            color="ブラック",
        )
    )
    _assert(row["SKU"] == "2754/3 BLACK", f"sku {row['SKU']}")
    _assert("①" not in str(row["SKU"]), "sku must not contain formula")
    _assert(str(row["価格計算式"]).startswith("①"), "calc_text in 価格計算式")
    _assert(str(row["価格計算式"]) != str(row["品代(海外仕入)"]), "calc is not just 品代")


def test_cheap_item_reprice() -> None:
    """品代 is always JPY — cheap EUR items must not be treated as EUR amounts."""
    clear_settings_cache()
    # EUR 2.00 → 品代 ¥371 (truncate)
    product_jpy = int(2.0 * 185.68)
    _assert(product_jpy == 371, f"expected 371 got {product_jpy}")
    src = source_amount_from_product_jpy(product_jpy, "EUR")
    _assert(abs(src - 2.0) < 0.02, f"source back {src}")

    expected = calculate_buyma_pricing(
        BuymaListingInput(
            brand_name="T",
            product_name="Cheap",
            source_url="https://example.com/p/2",
            shop_name="S",
            price=2.0,
            currency="EUR",
        ),
        profit_rate=0.05,
    )
    row = reprice_row_from_cost(
        {
            "品代(海外仕入)": str(product_jpy),
            "通貨": "EUR",
            "ブランド": "T",
            "商品名": "【T】Cheap 関税無＆送料無料",
            "仕入先URL": "https://example.com/p/2",
            "在庫": "1",
        },
        profit_rate=0.05,
    )
    _assert(row["品代(海外仕入)"] == str(expected.product_jpy), "品代 stable")
    _assert(row["出品価格"] == str(expected.listing_price), f"sell {row['出品価格']} != {expected.listing_price}")
    _assert(int(row["出品価格"]) < 50000, "must not explode to ~100k")

    # Bad inventory must not crash
    ok = reprice_row_from_cost(
        {"品代(海外仕入)": "18568", "通貨": "EUR", "在庫": "N/A", "商品名": "X", "ブランド": "T"},
        profit_rate=0.03,
    )
    _assert(ok["出品価格"], "reprice with bad stock")

    # USD path
    usd_jpy = int(10 * get_settings().usd_to_jpy_rate)
    usd_row = reprice_row_from_cost(
        {"品代(海外仕入)": str(usd_jpy), "通貨": "USD", "商品名": "U", "ブランド": "T", "在庫": "1"},
        profit_rate=0.03,
    )
    _assert(usd_row["品代(海外仕入)"] == str(usd_jpy), "USD 品代 stable")

    built = build_buyma_row(
        BuymaListingInput(
            brand_name="T",
            product_name="B",
            source_url="u",
            shop_name="S",
            price=10,
            currency="EUR",
        )
    )
    _assert(built.get("通貨") == "EUR", "build_buyma_row sets 通貨")


def test_filters() -> None:
    _assert(card_matches_filters({"brand": "Gucci", "href": "/bags/x", "name": "n"}, brand_filter="gucci"), "brand")
    _assert(not card_matches_filters({"brand": "Prada", "href": "/bags/x", "name": "n"}, brand_filter="gucci"), "brand neg")
    _assert(card_matches_filters({"brand": "X", "href": "/women/shoes/1", "name": "n"}, category_filter="shoes"), "cat")


def test_workbook_newest_wins_and_patch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "wb.xlsx"
        wb = Workbook()
        ws1 = wb.active
        ws1.title = "2026-08-01 (1)"
        headers = ["フォルダ名", "商品名", "仕入先URL", "商品コメント", "在庫監視", "Buyma公開"]
        ws1.append(headers)
        ws1.append(["F1", "Old Name", "https://ex.com/a", "OLD_COMMENT", "", ""])
        ws2 = wb.create_sheet("2026-08-13 (1)")
        ws2.append(headers)
        ws2.append(["F1", "New Name", "https://ex.com/a", "NEW_COMMENT", "", ""])
        wb.save(path)

        rows = read_all_workbook_rows(path)
        _assert(len(rows) == 1, f"dedupe {len(rows)}")
        _assert(rows[0]["商品名"] == "New Name", "newest sheet wins")
        _assert(rows[0]["商品コメント"] == "NEW_COMMENT", "keep newest comment")

        n = patch_workbook_fields(path, [("F1", {"在庫監視": "404:oos", "Buyma公開": "一時停止"})])
        _assert(n == 1, "patched")
        rows2 = read_all_workbook_rows(path)
        _assert(rows2[0]["在庫監視"] == "404:oos", "status patched")
        _assert(rows2[0]["商品コメント"] == "NEW_COMMENT", "comment not wiped")


def test_availability_heuristics() -> None:
    ok = evaluate_product_availability(_FakePage(body="Add to bag Size 40", stock=2), http_status=200)
    _assert(ok[1] is False, f"in stock {ok}")

    gone = evaluate_product_availability(_FakePage(status=404, body="404 not found", title="404", stock=0), http_status=404)
    _assert(gone[1] is True, f"404 {gone}")

    oos = evaluate_product_availability(
        _FakePage(body="sorry this item is sold out", title="Product", stock=0),
        http_status=200,
    )
    _assert(oos[1] is True and "oos" in oos[2], f"oos {oos}")

    cart = evaluate_product_availability(
        _FakePage(body="Add to cart — choose size", title="Product", stock=0),
        http_status=200,
    )
    _assert(cart[1] is False, f"cart present must not pause {cart}")


def test_merge_refresh() -> None:
    existing = ensure_row(
        {
            "フォルダ名": "F",
            "商品名": "Old",
            "出品URL": "https://buyma.com/item/1",
            "商品コメント": "KEEP_ME",
            "在庫": "0",
        }
    )
    refreshed = ensure_row({"フォルダ名": "F", "商品名": "New", "在庫": "3", "出品価格": "20000", "価格": "20000"})
    out = merge_refreshed_row(existing, refreshed)
    _assert(out["出品URL"] == "https://buyma.com/item/1", "keep buyma url")
    _assert(out["在庫"] == "3", "stock updated")
    _assert(out["商品名"] == "New", "name updated")
    _assert(out["在庫監視"].startswith("200:"), "monitor flag")


def test_load_monitor_csv() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.csv"
        write_products_csv(
            path,
            [{"商品名": "A", "ブランド": "B", "仕入先URL": "https://example.com/x", "フォルダ名": "F"}],
        )
        rows, kind = load_monitor_rows(path)
        _assert(kind == "csv" and len(rows) == 1, "csv load")
        _assert(isinstance(MonitorResult(hits=[], recovered=[]), MonitorResult), "result type")


def main() -> None:
    tests = [
        test_pricing_formula,
        test_sheet_examples,
        test_cheap_item_reprice,
        test_filters,
        test_workbook_newest_wins_and_patch,
        test_availability_heuristics,
        test_merge_refresh,
        test_load_monitor_csv,
    ]
    for fn in tests:
        fn()
        print(f"OK  {fn.__name__}")
    print("ALL_PASSED", len(tests))


if __name__ == "__main__":
    main()
