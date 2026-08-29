"""Share one sync Playwright instance per thread.

Playwright's Sync API leaves an asyncio loop running in the thread after the
first ``sync_playwright().start()``. Starting a second Sync API instance in the
same thread then raises:

    It looks like you are using Playwright Sync API inside the asyncio loop.

ChatGPT and EC scrapers must therefore share a single Playwright driver per
pipeline worker thread.
"""

from __future__ import annotations

import threading
from typing import Any

from playwright.sync_api import Playwright, sync_playwright

_lock = threading.Lock()
_state = threading.local()


def acquire_playwright() -> Playwright:
    with _lock:
        current: dict[str, Any] | None = getattr(_state, "current", None)
        if current is None:
            playwright = sync_playwright().start()
            _state.current = {"playwright": playwright, "refs": 1}
            return playwright
        current["refs"] += 1
        return current["playwright"]


def release_playwright() -> None:
    with _lock:
        current: dict[str, Any] | None = getattr(_state, "current", None)
        if current is None:
            return
        current["refs"] = max(0, int(current["refs"]) - 1)
        if current["refs"] > 0:
            return
        try:
            current["playwright"].stop()
        except Exception:  # noqa: BLE001
            pass
        _state.current = None


def playwright_ref_count() -> int:
    current: dict[str, Any] | None = getattr(_state, "current", None)
    if current is None:
        return 0
    return int(current["refs"])
