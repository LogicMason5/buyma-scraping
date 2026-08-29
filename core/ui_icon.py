"""Apply Buyma B icon to Tk / CustomTkinter windows (title bar + taskbar)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

APP_USER_MODEL_ID = "ECBuyma.UnifiedConsole"


def set_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:  # noqa: BLE001
        pass


def resolve_app_ico() -> Path | None:
    """Prefer .ico for Windows title/taskbar."""
    from core.paths import bundle_root, runtime_root

    candidates = [
        runtime_root() / "assets" / "app" / "ec_buyma.ico",
        bundle_root() / "assets" / "app" / "ec_buyma.ico",
        runtime_root() / "assets" / "ui" / "brand_mark.ico",
        bundle_root() / "assets" / "ui" / "brand_mark.ico",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def resolve_app_png() -> Path | None:
    from core.paths import bundle_root, runtime_root
    from core.ui_theme import asset_path

    candidates = [
        asset_path("brand_mark.png"),
        runtime_root() / "assets" / "app" / "ec_buyma_256.png",
        bundle_root() / "assets" / "app" / "ec_buyma_256.png",
        bundle_root() / "assets" / "ui" / "brand_mark.png",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return Path(path)
    return None


def apply_window_icon(window: Any) -> None:
    """Set window/taskbar icon to the black B mark."""
    set_app_user_model_id()
    ico = resolve_app_ico()
    if ico is not None:
        ico_s = str(ico)
        try:
            window._iconbitmap_method_called = True  # noqa: SLF001 — stop CTk default icon
        except Exception:  # noqa: BLE001
            pass
        for call in (
            lambda: window.iconbitmap(ico_s),
            lambda: window.wm_iconbitmap(ico_s),
            lambda: window.iconbitmap(default=ico_s),
            lambda: window.wm_iconbitmap(default=ico_s),
        ):
            try:
                call()
            except Exception:  # noqa: BLE001
                continue

    png = resolve_app_png()
    if png is not None:
        try:
            from PIL import Image, ImageTk

            img = Image.open(png).convert("RGBA")
            photos = []
            for size in (16, 32, 48, 64):
                photos.append(ImageTk.PhotoImage(img.resize((size, size), Image.Resampling.LANCZOS)))
            window._icon_photos = photos  # noqa: SLF001 — keep refs alive
            window.iconphoto(True, *photos)
        except Exception:  # noqa: BLE001
            pass


def patch_customtkinter_icons() -> None:
    """Make every CTk / CTkToplevel window use the B icon instead of Python/CTk."""
    ico = resolve_app_ico()
    if ico is None:
        return
    ico_s = str(ico)

    def _windows_set_titlebar_icon(self) -> None:  # noqa: ANN001
        try:
            self._iconbitmap_method_called = True
        except Exception:  # noqa: BLE001
            pass
        try:
            self.iconbitmap(ico_s)
        except Exception:  # noqa: BLE001
            try:
                self.wm_iconbitmap(ico_s)
            except Exception:  # noqa: BLE001
                pass
        apply_window_icon(self)

    try:
        from customtkinter.windows import ctk_tk, ctk_toplevel

        ctk_tk.CTk._windows_set_titlebar_icon = _windows_set_titlebar_icon
        ctk_toplevel.CTkToplevel._windows_set_titlebar_icon = _windows_set_titlebar_icon
    except Exception:  # noqa: BLE001
        pass
