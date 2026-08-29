"""EC site login → save secrets/ec_sessions/<site>/storage_state.json.

HTTP form POST first (no browser window). Azure WAF sites fall back to
background Chrome so the site's own JS check can complete.

Usage:
  py -3 scripts/ec_cookie_login.py julian-fashion
  py -3 scripts/ec_cookie_login.py --test julian-fashion
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.scrapers.sites import SCRAPER_REGISTRY
from core.sessions.cookie_login_service import EXIT_ERROR, EXIT_NEED_LOGIN, EXIT_OK, save_ec_login
from core.sessions.ec_session_service import has_saved_session
from core.utils.cookie_helper_log import log_cookie_event, run_cookie_helper


def _run_test(site_code: str) -> int:
    if has_saved_session(site_code):
        log_cookie_event(f"EC {site_code} test: cookie registered")
        return EXIT_OK
    log_cookie_event(f"EC {site_code} test: cookie empty")
    return EXIT_NEED_LOGIN


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    test_mode = False
    if args and args[0] == "--test":
        test_mode = True
        args = args[1:]
    if not args or args[0] in {"-h", "--help"}:
        codes = ", ".join(SCRAPER_REGISTRY.keys())
        print("Usage: py -3 scripts/ec_cookie_login.py [--test] <site_code>")
        print(f"Sites: {codes}")
        return EXIT_OK if args else EXIT_ERROR

    site_code = args[0].strip()
    scraper_cls = SCRAPER_REGISTRY.get(site_code)
    if not scraper_cls:
        print(f"Unknown site: {site_code}")
        print("Known:", ", ".join(SCRAPER_REGISTRY.keys()))
        return EXIT_ERROR

    label = f"ec-test:{site_code}" if test_mode else f"ec:{site_code}"
    fn = (lambda: _run_test(site_code)) if test_mode else (lambda: save_ec_login(site_code))
    return run_cookie_helper(label, fn)


if __name__ == "__main__":
    raise SystemExit(main())
