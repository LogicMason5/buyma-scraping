from __future__ import annotations

from slugify import slugify

SITE_FOLDER_ALIASES = {
    "julian-fashion": "julian",
    "montiboutique": "montiboutique",
    "minettiangeloonline": "minetti",
    "eleonorabonucci": "eleonorabonucci",
}


def site_folder_code(site_code: str) -> str:
    code = (site_code or "").strip().lower()
    if code in SITE_FOLDER_ALIASES:
        return SITE_FOLDER_ALIASES[code]
    return slugify(code, separator="") or "site"


def brand_folder_code(brand_name: str, brand_uid: str | None = None) -> str:
    raw = (brand_name or "").strip() or (brand_uid or "brand")
    # "Brand 0" -> "brand0", "Brand 1" -> "brand1"
    return slugify(raw, separator="") or slugify(brand_uid or "brand", separator="") or "brand"


def production_folder_name(site_code: str, brand_name: str, brand_uid: str | None = None) -> str:
    """Return folder name like EC-julian-brand0 / EC-montiboutique-brand1."""
    return f"EC-{site_folder_code(site_code)}-{brand_folder_code(brand_name, brand_uid)}"
