"""Copy shop notice images (98.png / 99.png) into product folders."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from core.config import get_settings
from core.paths import resolve_resource_path, seed_runtime_notice_assets

LogFn = Callable[[str], None]

# Common shop notice images uploaded by Engine3: AI(0) → 98 → EC → 99.
NOTICE_IMAGE_COPIES = (
    ("provided_image_1", "98.png"),  # 在庫確認
    ("provided_image_2", "99.png"),  # ラッピング
)


def ensure_product_notice_images(image_dir: Path, log: LogFn | None = None) -> bool:
    """Ensure 98.png / 99.png exist in ``image_dir``. Returns True if both are present."""
    _log = log or (lambda _m: None)
    image_dir = Path(image_dir)
    image_dir.mkdir(parents=True, exist_ok=True)
    seed_runtime_notice_assets()
    settings = get_settings()
    ok = True
    for attr, dest_name in NOTICE_IMAGE_COPIES:
        dest = image_dir / dest_name
        if dest.exists() and dest.stat().st_size > 1000:
            continue
        src = resolve_resource_path(getattr(settings, attr))
        if not src.exists() or not src.is_file():
            src = resolve_resource_path(Path(f"assets/{attr}.png"))
        if not src.exists() or not src.is_file():
            _log(f"共通画像なし: {src} → {dest_name} をスキップ")
            ok = False
            continue
        try:
            shutil.copy2(src, dest)
            _log(f"共通画像を保存: {dest_name}")
        except Exception as exc:  # noqa: BLE001
            _log(f"共通画像コピー失敗 ({dest_name}): {exc}")
            ok = False
    return ok
