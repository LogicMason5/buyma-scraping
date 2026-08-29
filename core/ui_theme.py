"""EC-Buyma visual theme — 'Line Flow' atelier pipeline.

Cool mist surfaces + ink navy brand + stage accents (teal / brass / rose-clay).
Avoids generic purple gradients and cream/serif defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import customtkinter as ctk

from core.config import get_settings


@dataclass(frozen=True)
class StagePalette:
    key: int
    code: str
    title_ja: str
    title_en: str
    blurb: str
    accent: str
    accent_hover: str
    soft: str
    icon: str


# Shared tokens
INK = "#E8EDF2"
INK_MUTED = "#9BA9B8"
MIST = "#050608"
SURFACE = "#0B0F14"
SURFACE_RAISED = "#11161D"
LINE = "#232D38"
ACCENT_TEAL = "#0D7377"
ACCENT_BRASS = "#C9842A"
ACCENT_ROSE = "#B84A4A"
DANGER = "#9B2C2C"
DANGER_HOVER = "#7A2222"
SUCCESS = "#1F7A4D"
GHOST = "#1A222D"
GHOST_HOVER = "#24303D"
LOG_BG = "#06090D"
LOG_FG = "#C7D3DF"

STAGES: tuple[StagePalette, ...] = (
    StagePalette(
        key=1,
        code="SCRAPE",
        title_ja="収集",
        title_en="Harvest",
        blurb="EC新着を取り込み · 重複除外 · 在庫集計",
        accent=ACCENT_TEAL,
        accent_hover="#0A5C5F",
        soft="#103135",
        icon="stage_1.png",
    ),
    StagePalette(
        key=2,
        code="STUDIO",
        title_ja="生成",
        title_en="Studio",
        blurb="モデル画像 0.png · CSVコメント更新",
        accent=ACCENT_BRASS,
        accent_hover="#A56A1E",
        soft="#332612",
        icon="stage_2.png",
    ),
    StagePalette(
        key=3,
        code="LIST",
        title_ja="出品",
        title_en="Launch",
        blurb="Buyma登録 · AI→98→EC→99 画像順",
        accent=ACCENT_ROSE,
        accent_hover="#943B3B",
        soft="#341B1B",
        icon="stage_3.png",
    ),
)


def ui_assets_dir() -> Path:
    settings = get_settings()
    return Path(settings.assets_dir) / "ui"


def asset_path(name: str) -> Path:
    from core.paths import bundle_root

    runtime = ui_assets_dir() / name
    if runtime.exists():
        return runtime
    bundled = bundle_root() / "assets" / "ui" / name
    if bundled.exists():
        return bundled
    # App icon fallback
    app_icon = bundle_root() / "assets" / "app" / "ec_buyma_256.png"
    if name == "brand_mark.png" and app_icon.exists():
        return app_icon
    return runtime


def ensure_ui_assets() -> Path:
    """Generate missing UI icons on first launch."""
    import shutil

    from core.paths import bundle_root

    out = ui_assets_dir()
    out.mkdir(parents=True, exist_ok=True)
    mark = out / "brand_mark.png"
    if not mark.exists():
        for src in (
            bundle_root() / "assets" / "ui" / "brand_mark.png",
            bundle_root() / "assets" / "app" / "ec_buyma_256.png",
        ):
            if src.exists():
                shutil.copy2(src, mark)
                break
        else:
            try:
                import importlib.util

                script = bundle_root() / "scripts" / "generate_ui_assets.py"
                if script.exists():
                    spec = importlib.util.spec_from_file_location("generate_ui_assets", script)
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        mod.main()
            except Exception:
                pass
    return out


def apply_global_theme() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    try:
        from core.ui_icon import patch_customtkinter_icons, set_app_user_model_id

        set_app_user_model_id()
        patch_customtkinter_icons()
    except Exception:  # noqa: BLE001
        pass


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    # Windows: Yu Gothic UI / Segoe UI — expressive enough without webfonts.
    family = "Yu Gothic UI"
    try:
        return ctk.CTkFont(family=family, size=size, weight=weight)
    except Exception:  # noqa: BLE001
        return ctk.CTkFont(size=size, weight=weight)


def font_display(size: int = 28) -> ctk.CTkFont:
    try:
        return ctk.CTkFont(family="Bahnschrift", size=size, weight="bold")
    except Exception:  # noqa: BLE001
        return font(size=size, weight="bold")


def stage_by_key(key: int) -> StagePalette:
    for s in STAGES:
        if s.key == key:
            return s
    return STAGES[0]
