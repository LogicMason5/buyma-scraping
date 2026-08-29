"""Log in to all EC sites + Buyma using .env credentials and save portable sessions.

Usage:
  py -3 scripts/bootstrap_all_sessions.py
  py -3 scripts/bootstrap_all_sessions.py --sync-dist
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import clear_settings_cache, get_settings
from core.scrapers.sites import SCRAPER_REGISTRY
from core.sessions.ec_session_service import has_saved_session, storage_state_path
from core.utils.cookie_helper_log import log_cookie_event


def bootstrap_ec_site(site_code: str) -> bool:
    from core.scrapers.playwright_base import PlaywrightSiteScraper

    scraper_cls = SCRAPER_REGISTRY[site_code]
    scraper: PlaywrightSiteScraper = scraper_cls()
    log_cookie_event(f"EC bootstrap start: {site_code}")
    try:
        scraper.start_browser(headless=False)
        scraper.login()
        scraper.page.wait_for_timeout(3000) if scraper.page else None
        ok = scraper.is_logged_in()
        if ok:
            scraper.logged_in = True
            scraper.persist_session()
            log_cookie_event(f"EC bootstrap OK: {site_code} -> {storage_state_path(site_code)}")
        else:
            log_cookie_event(f"EC bootstrap FAILED login check: {site_code}")
        return ok
    except Exception as exc:  # noqa: BLE001
        log_cookie_event(f"EC bootstrap ERROR {site_code}: {exc}")
        return False
    finally:
        scraper.close_browser()


def bootstrap_buyma() -> bool:
    from scripts.buyma_cookie_login import _run

    log_cookie_event("Buyma bootstrap start")
    code = _run(bootstrap=True)
    ok = code == 0
    log_cookie_event(f"Buyma bootstrap {'OK' if ok else 'FAILED'} exit={code}")
    return ok


def sync_to_dist(dist_root: Path) -> None:
    settings = get_settings()
    dist_root.mkdir(parents=True, exist_ok=True)
    # .env
    src_env = ROOT / ".env"
    if src_env.exists():
        shutil.copy2(src_env, dist_root / ".env")
    # secrets
    src_secrets = settings.secrets_dir
    dest_secrets = dist_root / "secrets"
    if dest_secrets.exists():
        shutil.rmtree(dest_secrets)
    if src_secrets.exists():
        shutil.copytree(src_secrets, dest_secrets)
    # workspace skeleton
    for name in ("scrape", "generate", "buyma"):
        (dist_root / "workspace" / name).mkdir(parents=True, exist_ok=True)
    log_cookie_event(f"Synced sessions to {dist_root}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap EC + Buyma sessions from .env")
    parser.add_argument("--sync-dist", action="store_true", help="Copy secrets/.env into dist/EC-Buyma")
    parser.add_argument("--skip-buyma", action="store_true")
    parser.add_argument("--only", nargs="*", help="Site codes (e.g. julian-fashion buyma)")
    args = parser.parse_args()

    clear_settings_cache()
    settings = get_settings()
    if not settings.ec_site_email or not settings.ec_site_password:
        print("EC_SITE_EMAIL / EC_SITE_PASSWORD missing in .env")
        return 1
    if not settings.buyma_account_email or not settings.buyma_account_password:
        print("BUYMA_ACCOUNT_EMAIL / BUYMA_ACCOUNT_PASSWORD missing in .env")
        return 1

    targets = list(SCRAPER_REGISTRY.keys())
    if args.only:
        targets = [c for c in args.only if c in SCRAPER_REGISTRY]

    results: dict[str, bool] = {}
    for site in targets:
        print(f"\n=== EC: {site} ===")
        results[site] = bootstrap_ec_site(site)
        print("OK" if results[site] else "FAILED")

    if not args.skip_buyma and (not args.only or "buyma" in args.only):
        print("\n=== Buyma ===")
        results["buyma"] = bootstrap_buyma()
        print("OK" if results["buyma"] else "FAILED")

    print("\n=== Summary ===")
    for name, ok in results.items():
        if name == "buyma":
            path = settings.buyma_cookies_path
            exists = path.exists() and path.stat().st_size > 100
        else:
            exists = has_saved_session(name)
            path = storage_state_path(name)
        print(f"  {name}: {'OK' if ok and exists else 'FAIL'} ({path})")

    if args.sync_dist:
        sync_to_dist(ROOT / "dist" / "EC-Buyma")
        print(f"\nPortable app updated: {ROOT / 'dist' / 'EC-Buyma'}")

    failed = [k for k, v in results.items() if not v]
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
