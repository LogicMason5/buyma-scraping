"""Probe ChatGPT file upload + image gen via HTTP."""

from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path

from curl_cffi import requests

from probe_chatgpt_http3 import UA, generate_proof_token

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    raw = json.loads((ROOT / "secrets" / "chatgpt_cookies.json").read_text(encoding="utf-8"))
    cookies = {c["name"]: c["value"] for c in raw["cookies"] if c.get("name") and c.get("value") is not None}
    s = requests.Session(impersonate="chrome131")
    s.cookies.update(cookies)
    s.headers.update({"User-Agent": UA, "Referer": "https://chatgpt.com/", "Origin": "https://chatgpt.com"})
    token = s.get("https://chatgpt.com/api/auth/session", timeout=45).json()["accessToken"]
    auth = {"Authorization": f"Bearer {token}"}

    # find a sample image
    imgs = list((ROOT / "workspace" / "generate").glob("*/1.webp"))
    if not imgs:
        imgs = list((ROOT / "workspace" / "generate").glob("*/0.png"))
    if not imgs:
        print("no sample image")
        return
    img = imgs[0]
    print("image", img, img.stat().st_size)
    mime = mimetypes.guess_type(str(img))[0] or "image/webp"

    # files endpoint
    meta = {
        "file_name": img.name,
        "file_size": img.stat().st_size,
        "use_case": "multimodal",
        "timezone_offset_min": -540,
        "reset_rate_limits": False,
    }
    r = s.post(
        "https://chatgpt.com/backend-api/files",
        headers={**auth, "Content-Type": "application/json"},
        json=meta,
        timeout=60,
    )
    print("files", r.status_code, r.text[:500])
    if r.status_code not in (200, 201):
        return
    info = r.json()
    file_id = info.get("file_id") or info.get("id")
    upload_url = info.get("upload_url")
    print("file_id", file_id, "upload_url", (upload_url or "")[:80])

    if upload_url:
        data = img.read_bytes()
        put = requests.put(
            upload_url,
            data=data,
            headers={"Content-Type": mime, "x-ms-blob-type": "BlockBlob"},
            timeout=120,
            impersonate="chrome131",
        )
        print("put", put.status_code, put.text[:200])

    # process
    if file_id:
        r2 = s.post(
            f"https://chatgpt.com/backend-api/files/{file_id}/process_upload_stream",
            headers={**auth, "Content-Type": "application/json", "Accept": "text/event-stream"},
            json={"file_id": file_id},
            timeout=120,
            stream=True,
        )
        print("process", r2.status_code)
        for i, line in enumerate(r2.iter_lines()):
            if i > 20:
                break
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            print(" ", line[:200])

    # conversation with attachment
    req = s.post(
        "https://chatgpt.com/backend-api/sentinel/chat-requirements",
        headers={**auth, "Content-Type": "application/json"},
        json={"p": ""},
        timeout=45,
    ).json()
    pow_info = req.get("proofofwork") or {}
    proof = generate_proof_token(pow_info.get("seed", ""), pow_info.get("difficulty", "0"))
    headers = {
        **auth,
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "Openai-Sentinel-Chat-Requirements-Token": req.get("token", ""),
        "Openai-Sentinel-Proof-Token": proof,
        "Openai-Sentinel-Turnstile-Token": "",
    }
    msg_id = str(uuid.uuid4())
    payload = {
        "action": "next",
        "messages": [
            {
                "id": msg_id,
                "author": {"role": "user"},
                "content": {
                    "content_type": "multimodal_text",
                    "parts": [
                        {
                            "content_type": "image_asset_pointer",
                            "asset_pointer": f"file-service://{file_id}",
                            "size_bytes": img.stat().st_size,
                            "width": 1024,
                            "height": 1024,
                        },
                        "Generate a simple fashion product photo of an Asian female model wearing this item. Reply briefly then generate image.",
                    ],
                },
                "metadata": {
                    "attachments": [
                        {
                            "id": file_id,
                            "name": img.name,
                            "size": img.stat().st_size,
                            "mime_type": mime,
                        }
                    ]
                },
            }
        ],
        "parent_message_id": str(uuid.uuid4()),
        "model": "gpt-5-5",
        "timezone_offset_min": -540,
        "history_and_training_disabled": True,
        "conversation_mode": {"kind": "primary_assistant"},
        "websocket_request_id": str(uuid.uuid4()),
    }
    r3 = s.post(
        "https://chatgpt.com/backend-api/f/conversation",
        headers=headers,
        json=payload,
        timeout=180,
        stream=True,
    )
    print("convo", r3.status_code)
    file_ids = []
    conv_id = None
    for line in r3.iter_lines():
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        if not line.startswith("data:"):
            continue
        data_s = line[5:].strip()
        if data_s == "[DONE]":
            break
        try:
            ev = json.loads(data_s)
        except Exception:
            continue
        if ev.get("conversation_id"):
            conv_id = ev["conversation_id"]
        msg = ev.get("message") or {}
        content = msg.get("content") or {}
        for part in content.get("parts") or []:
            if isinstance(part, dict) and part.get("asset_pointer"):
                file_ids.append(part["asset_pointer"])
                print("asset", part.get("asset_pointer"), part.get("content_type"))
        meta = msg.get("metadata") or {}
        if meta.get("image_gen_title") or "dalle" in str(meta).lower():
            print("meta_keys", list(meta.keys())[:20])
    print("conv_id", conv_id, "assets", file_ids[-5:])


if __name__ == "__main__":
    main()
