"""Verify Buyma color resolution (no その他→マルチカラー)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.buyma.buyma_browser_service import BuymaBrowserSession
from core.buyma.buyma_listing_service import resolve_buyma_color, resolve_listing_color


def main() -> int:
    assert resolve_buyma_color("Brown") == ("ブラウン（茶色）系", "ブラウン")
    assert resolve_buyma_color("その他")[0] == ""
    assert resolve_buyma_color("White bermuda shorts")[0] == "ホワイト（白）系"
    assert resolve_buyma_color("navy")[0] == "ネイビー（紺）系"

    # Comment boilerplate must not override CSV color (ホワイトデー bug).
    brown_row = {
        "カラー系統": "ブラウン（茶色）系",
        "色": "ブラウン",
        "商品名": "【Lemaire】Button cardigan 関税無＆送料無料",
        "フォルダ名": "LEMAIRE_Button_Cardigan_brown_339796",
        "商品コメント": "バレンタイン／ホワイトデー/母の日\n・カラー：ブラウン（茶色）系",
    }
    sys_b, name_b = resolve_listing_color(brown_row)
    assert sys_b == "ブラウン（茶色）系", sys_b
    assert name_b == "ブラウン", name_b
    gold_row = {
        "カラー系統": "ゴールド（金色）系",
        "色": "ゴールド",
        "商品名": "【Chloé】Paddington mini padlock earrings",
        "商品コメント": "バレンタイン／ホワイトデー/母の日",
    }
    sys_g, name_g = resolve_listing_color(gold_row)
    assert sys_g == "ゴールド（金色）系", sys_g
    assert name_g == "ゴールド", name_g

    # Product title must never become 色名 (Engine2 bug: カラー系統 + 商品名).
    title_row = {
        "カラー系統": "ブラウン（茶色）系",
        "色": "Button cardigan",
        "商品名": "【Lemaire】Button cardigan 関税無＆送料無料",
        "フォルダ名": "LEMAIRE_Button_Cardigan_brown_339796",
    }
    sys_t, name_t = resolve_listing_color(title_row)
    assert sys_t == "ブラウン（茶色）系", sys_t
    assert name_t == "ブラウン", name_t
    bag_row = {
        "カラー系統": "ブラウン（茶色）系",
        "色": "'Croissant Small' shoulder bag",
        "商品名": "【Lemaire】'Croissant Small' shoulder bag",
    }
    sys_bag, name_bag = resolve_listing_color(bag_row)
    assert (sys_bag, name_bag) == ("ブラウン（茶色）系", "ブラウン"), (sys_bag, name_bag)
    from core.buyma.buyma_listing_service import listing_stock_qty, resolve_buyma_color as rbc

    assert rbc("ブラウン（茶色）系", "Button cardigan") == ("ブラウン（茶色）系", "ブラウン")
    assert listing_stock_qty("244", ["S", "M"]) == 2
    assert listing_stock_qty("3", ["S", "M"]) == 3

    # Mock source fetch (Julian blocks plain HTTP with 403).
    row = {
        "カラー系統": "その他",
        "色": "",
        "商品名": "【Carhartt wip】'Quentin' jacket 関税無＆送料無料",
        "仕入先URL": (
            "https://www.julian-fashion.com/en-JP/product/340050/"
            "carhartt_wip/casual_jackets/quentin_jacket"
        ),
        "フォルダ名": "CARHARTT_WIP_Quentin_Jacket_na_340050",
    }
    system, name = resolve_listing_color(row, fetch_color=lambda _u: "Brown")
    print("mock resolve:", system, name)
    assert system == "ブラウン（茶色）系"
    assert name in {"Brown", "ブラウン"}

    # Live Playwright fetch via Buyma browser context.
    url = (
        "https://www.julian-fashion.com/en-JP/product/340050/"
        "carhartt_wip/casual_jackets/quentin_jacket"
    )
    session = BuymaBrowserSession()
    session.start()
    try:
        fetched = session._fetch_source_color(url)
        print("live fetch:", fetched)
        assert "brown" in fetched.lower(), fetched
        system2, name2 = resolve_listing_color(row, fetch_color=session._fetch_source_color)
        print("live resolve:", system2, name2)
        assert system2 == "ブラウン（茶色）系"
    finally:
        session.close()

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
