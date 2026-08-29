#!/usr/bin/env python3
"""Launch EC-Buyma (GUI) or helper cookie-login modes for the frozen .exe."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _set_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ECBuyma.UnifiedConsole")
    except Exception:  # noqa: BLE001
        pass


def _dispatch_helpers(argv: list[str]) -> bool:
    """Handle helper CLI flags used by the packaged .exe. Returns True if handled."""
    if len(argv) < 2:
        return False
    cmd = argv[1]
    if cmd == "--ec-cookie-login":
        site = argv[2] if len(argv) > 2 else ""
        from scripts.ec_cookie_login import main as ec_main

        raise SystemExit(ec_main([site] if site else []))
    if cmd == "--ec-cookie-test":
        site = argv[2] if len(argv) > 2 else ""
        from scripts.ec_cookie_login import main as ec_main

        raise SystemExit(ec_main(["--test", site] if site else ["--test"]))
    if cmd == "--buyma-cookie-login":
        from scripts.buyma_cookie_login import main as buyma_main

        raise SystemExit(buyma_main())
    if cmd == "--buyma-cookie-test":
        from scripts.buyma_cookie_login import main as buyma_main

        raise SystemExit(buyma_main(["--test"]))
    return False


def main() -> None:
    _set_app_id()
    from apps.launcher.app import main as launcher_main

    launcher_main()


if __name__ == "__main__":
    _set_app_id()
    if _dispatch_helpers(sys.argv):
        raise SystemExit(0)
    main()
