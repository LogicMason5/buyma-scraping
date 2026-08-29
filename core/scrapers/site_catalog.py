"""Per-EC-site brand / category catalogs for scrape target pickers."""

from __future__ import annotations

from dataclasses import dataclass


SITE_LABELS: dict[str, str] = {
    "julian-fashion": "Julian Fashion",
    "montiboutique": "Monti Boutique",
    "minettiangeloonline": "Minetti Angelo",
    "eleonorabonucci": "Eleonora Bonucci",
}


@dataclass(frozen=True)
class CatalogItem:
    id: str
    label: str
    keywords: str  # used by scrape filters (comma-safe token)
    url: str = ""


# Common luxury / contemporary brands across Julian / Monti / Eleonora / Minetti.
COMMON_BRANDS: tuple[CatalogItem, ...] = tuple(
    CatalogItem(id=slug, label=name, keywords=name)
    for name, slug in [
        ("Acne Studios", "acne-studios"),
        ("Alexander McQueen", "alexander-mcqueen"),
        ("Alexander Wang", "alexander-wang"),
        ("Ami Paris", "ami-paris"),
        ("Balenciaga", "balenciaga"),
        ("Balmain", "balmain"),
        ("Bottega Veneta", "bottega-veneta"),
        ("Burberry", "burberry"),
        ("Canada Goose", "canada-goose"),
        ("Carhartt WIP", "carhartt-wip"),
        ("Celine", "celine"),
        ("Chloe", "chloe"),
        ("Comme des Garcons", "comme-des-garcons"),
        ("Dior", "dior"),
        ("Dolce & Gabbana", "dolce-gabbana"),
        ("Fear of God", "fear-of-god"),
        ("Fendi", "fendi"),
        ("Givenchy", "givenchy"),
        ("Gucci", "gucci"),
        ("Hermes", "hermes"),
        ("Isabel Marant", "isabel-marant"),
        ("Jacquemus", "jacquemus"),
        ("Jil Sander", "jil-sander"),
        ("Kenzo", "kenzo"),
        ("Lemaire", "lemaire"),
        ("Loewe", "loewe"),
        ("Louis Vuitton", "louis-vuitton"),
        ("Maison Margiela", "maison-margiela"),
        ("Marni", "marni"),
        ("Max Mara", "max-mara"),
        ("Moncler", "moncler"),
        ("MSGM", "msgm"),
        ("Off-White", "off-white"),
        ("Palm Angels", "palm-angels"),
        ("Prada", "prada"),
        ("Rick Owens", "rick-owens"),
        ("Saint Laurent", "saint-laurent"),
        ("Salvatore Ferragamo", "salvatore-ferragamo"),
        ("Stone Island", "stone-island"),
        ("The Row", "the-row"),
        ("Thom Browne", "thom-browne"),
        ("Tod's", "tods"),
        ("Valentino", "valentino"),
        ("Versace", "versace"),
        ("Vetements", "vetements"),
        ("Y-3", "y-3"),
    ]
)


def _j(path: str) -> str:
    return f"https://www.julian-fashion.com/en-JP/{path}"


def _m(path: str) -> str:
    return f"https://www.montiboutique.com/en-JP/{path}"


def _e(path: str) -> str:
    return f"https://eleonorabonucci.com/en/{path}"


def _a(path: str) -> str:
    return f"https://www.angelominetti.it/{path}"


SITE_CATEGORIES: dict[str, tuple[CatalogItem, ...]] = {
    "julian-fashion": (
        CatalogItem("women-new", "Women · New", "women/new", _j("women/new")),
        CatalogItem("men-new", "Men · New", "men/new", _j("men/new")),
        CatalogItem("women-clothing", "Women · Clothing", "women/clothing", _j("women/clothing")),
        CatalogItem("men-clothing", "Men · Clothing", "men/clothing", _j("men/clothing")),
        CatalogItem("women-bags", "Women · Bags", "women/bags", _j("women/bags")),
        CatalogItem("men-bags", "Men · Bags", "men/bags", _j("men/bags")),
        CatalogItem("women-shoes", "Women · Shoes", "women/shoes", _j("women/shoes")),
        CatalogItem("men-shoes", "Men · Shoes", "men/shoes", _j("men/shoes")),
        CatalogItem("women-accessories", "Women · Accessories", "women/accessories", _j("women/accessories")),
        CatalogItem("men-accessories", "Men · Accessories", "men/accessories", _j("men/accessories")),
        CatalogItem("women-dresses", "Women · Dresses", "women/clothing/dresses", _j("women/clothing/dresses")),
        CatalogItem("women-jackets", "Women · Jackets", "women/clothing/jackets", _j("women/clothing/jackets")),
        CatalogItem("women-knitwear", "Women · Knitwear", "women/clothing/knitwear", _j("women/clothing/knitwear")),
        CatalogItem("men-jackets", "Men · Jackets", "men/clothing/jackets", _j("men/clothing/jackets")),
        CatalogItem("men-knitwear", "Men · Knitwear", "men/clothing/knitwear", _j("men/clothing/knitwear")),
    ),
    "montiboutique": (
        CatalogItem("women-new", "Women · New", "women/new", _m("women/new")),
        CatalogItem("men-new", "Men · New", "men/new", _m("men/new")),
        CatalogItem("women-clothing", "Women · Clothing", "women/clothing", _m("women/clothing")),
        CatalogItem("men-clothing", "Men · Clothing", "men/clothing", _m("men/clothing")),
        CatalogItem("women-bags", "Women · Bags", "women/bags", _m("women/bags")),
        CatalogItem("men-bags", "Men · Bags", "men/bags", _m("men/bags")),
        CatalogItem("women-shoes", "Women · Shoes", "women/shoes", _m("women/shoes")),
        CatalogItem("men-shoes", "Men · Shoes", "men/shoes", _m("men/shoes")),
        CatalogItem("women-accessories", "Women · Accessories", "women/accessories", _m("women/accessories")),
        CatalogItem("men-accessories", "Men · Accessories", "men/accessories", _m("men/accessories")),
    ),
    "eleonorabonucci": (
        CatalogItem("women-newin", "Women · New In", "women/newin", _e("women/newin")),
        CatalogItem("men-newin", "Men · New In", "men/newin", _e("men/newin")),
        CatalogItem("women-clothing", "Women · Clothing", "women/clothing", _e("women/new-collection/clothing")),
        CatalogItem("women-dresses", "Women · Dresses", "clothing/dresses", _e("women/new-collection/clothing/dresses")),
        CatalogItem("women-bags", "Women · Bags", "women/bags", _e("women/bags")),
        CatalogItem("men-bags", "Men · Bags", "men/bags", _e("men/bags")),
        CatalogItem("women-shoes", "Women · Shoes", "women/shoes", _e("women/shoes")),
        CatalogItem("men-shoes", "Men · Shoes", "men/shoes", _e("men/shoes")),
        CatalogItem("women-accessories", "Women · Accessories", "women/accessories", _e("women/accessories")),
        CatalogItem("kids", "Kids", "kids", _e("kids")),
    ),
    "minettiangeloonline": (
        CatalogItem("woman", "Woman", "woman", _a("woman/")),
        CatalogItem("man", "Man", "man", _a("man/")),
        CatalogItem("woman-new", "Woman · New", "woman/clothing/new", _a("woman/clothing/new/")),
        CatalogItem("woman-dresses", "Woman · Dresses", "woman/clothing/dresses", _a("woman/clothing/dresses/")),
        CatalogItem("woman-clothing", "Woman · Clothing", "woman/clothing", _a("woman/clothing/")),
        CatalogItem("man-clothing", "Man · Clothing", "man/clothing", _a("man/clothing/")),
        CatalogItem("woman-bags", "Woman · Bags", "woman/bags", _a("woman/bags/")),
        CatalogItem("man-bags", "Man · Bags", "man/bags", _a("man/bags/")),
        CatalogItem("woman-shoes", "Woman · Shoes", "woman/shoes", _a("woman/shoes/")),
        CatalogItem("man-shoes", "Man · Shoes", "man/shoes", _a("man/shoes/")),
    ),
}

SITE_BRANDS: dict[str, tuple[CatalogItem, ...]] = {
    code: COMMON_BRANDS for code in SITE_CATEGORIES
}


def site_label(code: str) -> str:
    return SITE_LABELS.get(code, code)


def all_site_codes() -> list[str]:
    return list(SITE_CATEGORIES.keys())


def brands_for(site_code: str) -> tuple[CatalogItem, ...]:
    return SITE_BRANDS.get(site_code, COMMON_BRANDS)


def categories_for(site_code: str) -> tuple[CatalogItem, ...]:
    return SITE_CATEGORIES.get(site_code, ())


def category_urls_for(site_code: str, selected_ids_or_keywords: list[str]) -> list[str]:
    """Resolve selected category ids/keywords to listing URLs."""
    if not selected_ids_or_keywords:
        return []
    wanted = {x.strip().lower() for x in selected_ids_or_keywords if x and x.strip()}
    urls: list[str] = []
    for item in categories_for(site_code):
        keys = {item.id.lower(), item.keywords.lower(), item.label.lower()}
        if keys & wanted or any(w in item.keywords.lower() or w in item.id.lower() for w in wanted):
            if item.url:
                urls.append(item.url)
    return urls
