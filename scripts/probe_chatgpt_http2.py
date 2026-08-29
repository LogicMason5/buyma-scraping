"""Deeper ChatGPT HTTP probe: models, requirements, short conversation."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from curl_cffi import requests

ROOT = Path(__file__).resolve().parents[1]


def load_cookie_dict() -> dict[str, str]:
    raw = json.loads((ROOT / "secrets" / "chatgpt_cookies.json").read_text(encoding="utf-8"))
    cookies = raw["cookies"] if isinstance(raw, dict) else raw
    out: dict[str, str] = {}
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        if name and value is not None:
            out[str(name)] = str(value)
    return out


def main() -> None:
    cookies = load_cookie_dict()
    s = requests.Session(impersonate="chrome131")
    s.cookies.update(cookies)
    s.headers.update(
        {
            "Accept": "application/json",
            "Referer": "https://chatgpt.com/",
            "Origin": "https://chatgpt.com",
        }
    )
    r = s.get("https://chatgpt.com/api/auth/session", timeout=45)
    print("session", r.status_code)
    data = r.json()
    token = data["accessToken"]
    print("email", (data.get("user") or {}).get("email"))
    auth = {"Authorization": f"Bearer {token}"}

    r2 = s.get("https://chatgpt.com/backend-api/models", headers=auth, timeout=45)
    print("models", r2.status_code, r2.text[:300])
    models = []
    try:
        models = [m.get("slug") for m in (r2.json().get("models") or [])][:15]
    except Exception:
        pass
    print("model_slugs", models)

    # chat requirements
    r3 = s.post(
        "https://chatgpt.com/backend-api/sentinel/chat-requirements",
        headers={**auth, "Content-Type": "application/json"},
        json={"conversation_mode_kind": "primary_assistant"},
        timeout=45,
    )
    print("requirements", r3.status_code, r3.text[:500])
    req = r3.json() if r3.status_code == 200 else {}
    token_y = req.get("token") or req.get("chat_requirements_token")
    print("req_token_len", len(token_y or ""))

    # Try a tiny conversation
    conversation_id = None
    parent = str(uuid.uuid4())
    msg_id = str(uuid.uuid4())
    model = "gpt-5" if "gpt-5" in (models or []) else (models[0] if models else "auto")
    # prefer gpt-4o / auto
    for cand in ("gpt-4o", "auto", "text-davinci-002-render-sha"):
        if cand in (models or []) or cand == "auto":
            model = cand
            break
    if models:
        # pick first non-deprecated looking
        model = models[0]
    print("using_model", model)

    payload = {
        "action": "next",
        "messages": [
            {
                "id": msg_id,
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": ["Reply with exactly: OK"]},
                "metadata": {},
            }
        ],
        "parent_message_id": parent,
        "model": model,
        "timezone_offset_min": -540,
        "history_and_training_disabled": True,
        "conversation_mode": {"kind": "primary_assistant"},
        "websocket_request_id": str(uuid.uuid4()),
    }
    headers = {
        **auth,
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }
    if token_y:
        headers["Openai-Sentinel-Chat-Requirements-Token"] = token_y

    r4 = s.post(
        "https://chatgpt.com/backend-api/conversation",
        headers=headers,
        json=payload,
        timeout=120,
        stream=True,
    )
    print("conversation", r4.status_code, r4.headers.get("content-type"))
    text_chunks = []
    final_parts = []
    for line in r4.iter_lines():
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
        msg = ev.get("message") or {}
        content = msg.get("content") or {}
        parts = content.get("parts") or []
        if parts and msg.get("author", {}).get("role") == "assistant":
            final_parts = parts
        if ev.get("type") == "message" or parts:
            text_chunks.append(str(parts[-1]) if parts else "")
    print("assistant", (final_parts[-1] if final_parts else "")[:200])
    print("error_body_if_any", "" if r4.status_code == 200 else r4.text[:500])


if __name__ == "__main__":
    main()
