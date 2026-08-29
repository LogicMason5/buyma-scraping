"""Buyma login → save secrets/buyma_cookies.json.

HTTP form POST first (no browser window). Falls back to background Chrome
if the login page requires a browser captcha.

Usage:
  py -3 scripts/buyma_cookie_login.py
  py -3 scripts/buyma_cookie_login.py --test
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import clear_settings_cache, get_settings
from core.sessions.cookie_login_service import EXIT_NEED_LOGIN, EXIT_OK, save_buyma_login
from core.utils.cookie_helper_log import log_cookie_event, run_cookie_helper


def _run(*, test_mode: bool = False) -> int:
    clear_settings_cache()
    settings = get_settings()
    if test_mode:
        path = settings.buyma_cookies_path
        if path.exists() and path.stat().st_size > 10:
            log_cookie_event("Buyma test: cookie registered")
            return EXIT_OK
        log_cookie_event("Buyma test: cookie empty")
        return EXIT_NEED_LOGIN
    return save_buyma_login()


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    test_mode = bool(args and args[0] == "--test")
    label = "buyma-test" if test_mode else "buyma"
    return run_cookie_helper(label, lambda: _run(test_mode=test_mode))


if __name__ == "__main__":
    raise SystemExit(main())
