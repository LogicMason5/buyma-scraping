"""Manual ChatGPT login → save secrets/chatgpt_cookies.json.

Open Chrome, log in yourself, then press Enter here to save cookies.
Runtime engines use the saved cookies only (no password auto-login).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.chatgpt.chatgpt_cookie_service import apply_cookies, save_cookies_to_file
from core.config import clear_settings_cache, get_settings
from core.utils.chrome_profile import prepare_chrome_profile
from core.utils.playwright_runtime import acquire_playwright, release_playwright


def main() -> None:
    clear_settings_cache()
    settings = get_settings()
    profile = Path(settings.chatgpt_profile_path)
    prepare_chrome_profile(profile)
    print("ChatGPT に手動でログインしてください。完了したらこの画面で Enter を押すと cookie を保存します。")
    print(f"Profile: {profile}")
    print(f"Cookies: {settings.chatgpt_cookies_path}")
    pw = acquire_playwright()
    context = None
    try:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="chrome",
            headless=False,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            viewport=None,
            ignore_default_args=["--enable-automation"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        apply_cookies(context, settings.chatgpt_cookies_path)
        page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
        input("Press Enter after login… ")
        saved = save_cookies_to_file(context, settings.chatgpt_cookies_path)
        print(f"Saved {saved.get('cookie_count')} cookies → {saved.get('path')}")
        if saved.get("skipped"):
            print(f"Note: save skipped ({saved.get('reason')})")
    finally:
        if context:
            context.close()
        release_playwright()


if __name__ == "__main__":
    main()
