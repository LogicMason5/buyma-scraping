"""Resolve project roots for source runs and frozen (.exe) builds."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def runtime_root() -> Path:
    """Writable root: directory containing the .exe, or the repo root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundle_root() -> Path:
    """Read-only bundled resources (PyInstaller extract dir or repo root)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[1]


def resolve_resource_path(relative_or_abs: str | Path) -> Path:
    """Prefer a writable file next to the exe; fall back to PyInstaller _internal."""
    p = Path(relative_or_abs)
    if p.is_absolute():
        if p.exists():
            return p
        try:
            rel = p.relative_to(runtime_root())
        except ValueError:
            return p
        bundled = (bundle_root() / rel).resolve()
        return bundled if bundled.exists() else p
    runtime = (runtime_root() / p).resolve()
    if runtime.exists():
        return runtime
    bundled = (bundle_root() / p).resolve()
    if bundled.exists():
        return bundled
    return runtime


def seed_runtime_notice_assets() -> None:
    """Copy bundled 98/99 source PNGs next to the exe so they can be replaced."""
    dest_dir = runtime_root() / "assets"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in ("provided_image_1.png", "provided_image_2.png", "brand_intro_image.png"):
        dest = dest_dir / name
        if dest.exists() and dest.stat().st_size > 1000:
            continue
        src = bundle_root() / "assets" / name
        if src.exists() and src.is_file():
            try:
                shutil.copy2(src, dest)
            except Exception:
                pass
