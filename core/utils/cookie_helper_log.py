"""Append helper output to logs/cookie_login.log next to the exe."""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

from core.paths import runtime_root


def cookie_log_path() -> Path:
    path = runtime_root() / "logs" / "cookie_login.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def log_cookie_event(message: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}\n"
    try:
        with cookie_log_path().open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:  # noqa: BLE001
        pass
    print(message, flush=True)


def run_cookie_helper(label: str, fn) -> int:
    """Run a cookie helper and always leave a trace in logs/."""
    log_cookie_event(f"START {label}")
    try:
        code = int(fn())
        log_cookie_event(f"END {label} exit={code}")
        return code
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        log_cookie_event(f"ERROR {label}: {exc}\n{tb}")
        try:
            from core.utils.user_confirm import notify_user

            notify_user(
                "登録が完了していません",
                "クッキーを使用して手動でログインを行ってください。\n\n"
                f"{exc}\n\n詳細: {cookie_log_path()}",
            )
        except Exception:  # noqa: BLE001
            pass
        return 1
