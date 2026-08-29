"""Try conversation with PoW + requirements token (curl_cffi, no Chrome)."""

from __future__ import annotations

import base64
import hashlib
import json
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests

ROOT = Path(__file__).resolve().parents[1]
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def generate_proof_token(seed: str, difficulty: str, user_agent: str = UA) -> str:
    screen = random.choice([3008, 4010, 6000]) * random.choice([1, 2, 4])
    now_utc = datetime.now(timezone.utc)
    parse_time = now_utc.strftime("%a, %d %b %Y %H:%M:%S GMT")
    proof_token = [
        screen,
        parse_time,
        None,
        0,
        user_agent,
        "https://tcr9i.chat.openai.com/v2/35536E1E-65B4-4D96-9D97-6ADB7EFF8147/api.js",
        "dpl=1440a687921de39ff5ee56b92807faaadce73f13",
        "en",
        "en-US",
        None,
        "plugins−[object PluginArray]",
        random.choice(
            [
                "_reactListeningcfilawjnerp",
                "_reactListening9ne2dfo1i47",
                "_reactListening410nzwhan2a",
            ]
        ),
        random.choice(["alert", "ontransitionend", "onprogress"]),
    ]
    diff_len = len(difficulty)
    for i in range(500_000):
        proof_token[3] = i
        base = base64.b64encode(json.dumps(proof_token, separators=(",", ":")).encode()).decode()
        digest = hashlib.sha3_512((seed + base).encode()).hexdigest()
        if digest[:diff_len] <= difficulty:
            return "gAAAAAB" + base
    fallback = base64.b64encode(f'"{seed}"'.encode()).decode()
    return "gAAAAABwQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D" + fallback


def main() -> None:
    raw = json.loads((ROOT / "secrets" / "chatgpt_cookies.json").read_text(encoding="utf-8"))
    cookies = {c["name"]: c["value"] for c in raw["cookies"] if c.get("name") and c.get("value") is not None}
    s = requests.Session(impersonate="chrome131")
    s.cookies.update(cookies)
    s.headers.update({"User-Agent": UA, "Referer": "https://chatgpt.com/", "Origin": "https://chatgpt.com"})

    token = s.get("https://chatgpt.com/api/auth/session", timeout=45).json()["accessToken"]
    auth = {"Authorization": f"Bearer {token}"}

    req = s.post(
        "https://chatgpt.com/backend-api/sentinel/chat-requirements",
        headers={**auth, "Content-Type": "application/json"},
        json={"p": ""},
        timeout=45,
    ).json()
    print("turnstile_required", (req.get("turnstile") or {}).get("required"))
    print("pow_required", (req.get("proofofwork") or {}).get("required"))
    print("so_required", (req.get("so") or {}).get("required"))

    pow_info = req.get("proofofwork") or {}
    proof = generate_proof_token(pow_info.get("seed", ""), pow_info.get("difficulty", "0"))
    print("proof_len", len(proof))

    headers = {
        **auth,
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "Openai-Sentinel-Chat-Requirements-Token": req.get("token", ""),
        "Openai-Sentinel-Proof-Token": proof,
    }
    # Some clients send empty turnstile when not available
    headers["Openai-Sentinel-Turnstile-Token"] = ""

    payload = {
        "action": "next",
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": ["Reply with exactly: OK"]},
                "metadata": {},
            }
        ],
        "parent_message_id": str(uuid.uuid4()),
        "model": "gpt-5-5",
        "timezone_offset_min": -540,
        "history_and_training_disabled": True,
        "conversation_mode": {"kind": "primary_assistant"},
        "websocket_request_id": str(uuid.uuid4()),
    }

    for endpoint in (
        "https://chatgpt.com/backend-api/f/conversation",
        "https://chatgpt.com/backend-api/conversation",
    ):
        r = s.post(endpoint, headers=headers, json=payload, timeout=120, stream=True)
        print("endpoint", endpoint, "status", r.status_code, "ct", r.headers.get("content-type"))
        body_preview = ""
        assistant = ""
        try:
            for line in r.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                if line.startswith("data:"):
                    data_s = line[5:].strip()
                    if data_s == "[DONE]":
                        break
                    try:
                        ev = json.loads(data_s)
                    except Exception:
                        continue
                    msg = ev.get("message") or {}
                    parts = (msg.get("content") or {}).get("parts") or []
                    if parts and (msg.get("author") or {}).get("role") == "assistant":
                        assistant = str(parts[-1])
                else:
                    body_preview += line[:200]
        except Exception as exc:  # noqa: BLE001
            print("stream_err", exc)
        if r.status_code != 200:
            try:
                print("err", r.text[:400])
            except Exception:
                print("err_preview", body_preview[:400])
        print("assistant", assistant[:200])
        if assistant:
            break


if __name__ == "__main__":
    main()
