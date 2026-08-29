"""Diagnose / fix-verify ChatGPT image download picking the wrong (old) asset.

Reproduces the bug: shared image conversation has many assets; old code picked the
largest by size_bytes → same socks image for every product.

Usage:
  py -3 scripts/test_chatgpt_image_download.py
  py -3 scripts/test_chatgpt_image_download.py --regen 2
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.chatgpt.chatgpt_http_service import ChatGPTHttpSession
from core.config import get_settings
from core.prompts import default_image_prompt, safe_format


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _fmt_time(ts: float) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def diagnose(session: ChatGPTHttpSession, conv_id: str) -> list[dict]:
    conv = session._conversation_from_api(conv_id)
    _, imgs = session._extract_from_conversation(conv)
    print(f"\n=== Image conversation {conv_id} ===")
    print(f"Total image assets: {len(imgs)}")
    for i, a in enumerate(imgs[:15]):
        fid = a.get("_file_id") or session._file_id_from_pointer(a.get("asset_pointer") or "")
        print(
            f"  [{i}] size={a.get('size_bytes')} create={_fmt_time(float(a.get('_create_time') or 0))} "
            f"id={fid[:40]}"
        )

    by_size = sorted(imgs, key=lambda a: int(a.get("size_bytes") or 0), reverse=True)
    by_time = session.pick_newest_image_asset(imgs)
    old_pick = by_size[0] if by_size else None
    print("\n--- OLD picker (largest size) ---")
    if old_pick:
        print(
            f"  size={old_pick.get('size_bytes')} create={_fmt_time(float(old_pick.get('_create_time') or 0))} "
            f"id={old_pick.get('_file_id')}"
        )
    print("--- NEW picker (newest create_time) ---")
    if by_time:
        print(
            f"  size={by_time.get('size_bytes')} create={_fmt_time(float(by_time.get('_create_time') or 0))} "
            f"id={by_time.get('_file_id')}"
        )
    if old_pick and by_time and old_pick.get("_file_id") != by_time.get("_file_id"):
        print("\nBUG CONFIRMED: largest != newest (this caused identical 0.png across folders).")
    elif old_pick and by_time:
        print("\nLargest and newest currently match (history may be short).")
    return imgs


def download_newest_n(session: ChatGPTHttpSession, conv_id: str, imgs: list[dict], n: int, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Unique by file_id, newest first (already sorted)
    seen: set[str] = set()
    unique: list[dict] = []
    for a in imgs:
        fid = str(a.get("_file_id") or "")
        if not fid or fid in seen:
            continue
        seen.add(fid)
        unique.append(a)
        if len(unique) >= n:
            break

    paths: list[Path] = []
    print(f"\n=== Download newest {len(unique)} distinct assets → {out_dir} ===")
    for i, a in enumerate(unique):
        fid = str(a.get("_file_id"))
        dest = out_dir / f"newest_{i}_{fid[:12]}.png"
        session.download_file(fid, conv_id, dest)
        print(f"  saved {dest.name} size={dest.stat().st_size} sha={_sha(dest)}")
        paths.append(dest)
    if len(paths) >= 2:
        same = _sha(paths[0]) == _sha(paths[1])
        print(f"\nHash compare newest[0] vs newest[1]: {'SAME (bad)' if same else 'DIFFERENT (good)'}")
    return paths


def regen_products(session: ChatGPTHttpSession, count: int) -> None:
    settings = get_settings()
    conv_id = session.conversation_id_from_url(settings.chatgpt_image_project_url)
    folders = [
        ROOT
        / "workspace/scrape/run_20260811_010552/images/A_P_C_Georges_Blouson_na_338912",
        ROOT
        / "workspace/scrape/run_20260811_010552/images/A_P_C_Sima_Sweater_na_339358",
        ROOT
        / "workspace/scrape/run_20260811_010552/images/A_P_C_Sima_Sweater_na_339359",
    ]
    folders = [f for f in folders if f.is_dir()][:count]
    prompt_t = default_image_prompt()
    hashes: list[str] = []
    for folder in folders:
        src = None
        for name in ("1.webp", "1.jpg", "1.png", "2.webp"):
            p = folder / name
            if p.exists():
                src = p
                break
        if src is None:
            print(f"skip {folder.name}: no source image")
            continue
        print(f"\nRegen {folder.name} from {src.name} …")
        before = set(session.snapshot_conversation_image_ids(conv_id))
        print(f"  known image ids before: {len(before)}")
        result = session.generate_image_only(
            image_prompt=prompt_t,
            output_dir=folder,
            source_product_image=src,
            prompt_vars={
                "brand_name": "A.P.C.",
                "product_name": folder.name,
                "site_name": "Julian Fashion",
                "source_url": "",
                "product_code": "",
                "price_text": "",
                "reference_price_text": "",
                "category": "",
                "material": "確認中",
                "origin_country": "イタリア",
                "color": "指定なし",
                "size": "指定なし",
                "source_description": "",
            },
            conversation_id=conv_id,
            on_step=lambda n, m: print(f"  · {n}: {m}"),
        )
        if not result.success:
            print(f"  FAIL: {result.error_message}")
            continue
        out = folder / "0.png"
        h = _sha(out)
        hashes.append(h)
        print(f"  OK → {out} size={out.stat().st_size} sha={h}")
    if len(hashes) >= 2:
        print(
            f"\nRegen hash compare: "
            f"{'SAME (still broken)' if hashes[0] == hashes[1] else 'DIFFERENT (fixed)'}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--regen", type=int, default=0, help="Also regenerate N product folders")
    args = parser.parse_args()

    settings = get_settings()
    conv_id = ChatGPTHttpSession.conversation_id_from_url(settings.chatgpt_image_project_url)
    if not conv_id:
        print("No conversation id in CHATGPT_IMAGE_PROJECT_URL")
        return 1

    session = ChatGPTHttpSession()
    session.start()
    try:
        imgs = diagnose(session, conv_id)
        out = ROOT / "workspace" / "generate" / "_image_download_test"
        download_newest_n(session, conv_id, imgs, n=2, out_dir=out)
        if args.regen > 0:
            regen_products(session, args.regen)
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
