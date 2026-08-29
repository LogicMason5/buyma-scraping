from core.scrapers.sites.eleonora_bonucci import EleonoraBonucciScraper
from core.scrapers.sites.julian_fashion import JulianFashionScraper
from core.scrapers.sites.minetti_angelo import MinettiAngeloScraper
from core.scrapers.sites.monti_boutique import MontiBoutiqueScraper

SCRAPER_REGISTRY = {
    "julian-fashion": JulianFashionScraper,
    "montiboutique": MontiBoutiqueScraper,
    "minettiangeloonline": MinettiAngeloScraper,
    "eleonorabonucci": EleonoraBonucciScraper,
}
