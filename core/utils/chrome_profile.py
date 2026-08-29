"""Chrome profile helpers for Playwright persistent contexts."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

LOCK_NAMES = (
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
    "lockfile",
)


def prepare_chrome_profile(profile_dir: Path) -> Path:
    """Ensure profile dir exists and clear stale Chrome locks."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    _kill_chrome_using_profile(profile_dir)
    time.sleep(0.4)
    for name in LOCK_NAMES:
        lock_path = profile_dir / name
        try:
            if lock_path.exists():
                lock_path.unlink()
        except OSError as exc:
            logger.warning("Could not remove profile lock %s: %s", lock_path, exc)
    disable_chrome_password_manager(profile_dir)
    return profile_dir


def disable_chrome_password_manager(profile_dir: Path) -> None:
    """Turn off Chrome's 'save password?' bubble for automation profiles."""
    import json

    default_dir = profile_dir / "Default"
    default_dir.mkdir(parents=True, exist_ok=True)
    prefs_path = default_dir / "Preferences"
    prefs: dict = {}
    if prefs_path.exists():
        try:
            loaded = json.loads(prefs_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                prefs = loaded
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read Chrome Preferences %s: %s", prefs_path, exc)
    prefs["credentials_enable_service"] = False
    profile = prefs.get("profile")
    if not isinstance(profile, dict):
        profile = {}
    profile["password_manager_enabled"] = False
    profile["password_manager_leak_detection"] = False
    prefs["profile"] = profile
    password_manager = prefs.get("password_manager")
    if not isinstance(password_manager, dict):
        password_manager = {}
    password_manager["saving_enabled"] = False
    prefs["password_manager"] = password_manager
    try:
        prefs_path.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not write Chrome Preferences %s: %s", prefs_path, exc)


def _kill_chrome_using_profile(profile_dir: Path) -> None:
    """Best-effort kill of Chrome processes that hold this user-data-dir."""
    target = str(profile_dir.resolve()).lower()
    try:
        # Use WMIC/PowerShell-free approach via tasklist is unreliable for args.
        # PowerShell Get-CimInstance works on this Windows host.
        ps = (
            "$target='"
            + target.replace("'", "''")
            + "'; "
            + "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            + "ForEach-Object { "
            + "  if ($_.CommandLine -and $_.CommandLine.ToLower().Contains($target)) { "
            + "    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} "
            + "  } "
            + "}"
        )
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Chrome profile process cleanup failed: %s", exc)


def reset_profile_if_corrupt(profile_dir: Path) -> None:
    """If profile repeatedly fails to launch, wipe and recreate."""
    if not profile_dir.exists():
        profile_dir.mkdir(parents=True, exist_ok=True)
        return
    try:
        shutil.rmtree(profile_dir, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to reset profile %s: %s", profile_dir, exc)
    profile_dir.mkdir(parents=True, exist_ok=True)
