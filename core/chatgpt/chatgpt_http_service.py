"""ChatGPT generation via cookie HTTP (curl_cffi) — no Chrome window.

Unofficial web backend; may break when OpenAI changes APIs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import random
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from curl_cffi import requests

from core.chatgpt.chatgpt_browser_service import ChatGPTGenerationResult
from core.chatgpt.chatgpt_cookie_service import load_cookies_from_file
from core.config import get_settings
from core.prompts import safe_format

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

# Cloudflare currently challenges older curl_cffi fingerprints (e.g. chrome131).
# Prefer profiles that still pass /api/auth/session on this network.
IMPERSONATE_CANDIDATES = (
    "chrome142",
    "chrome136",
    "chrome131",
    "chrome124",
    "chrome",
)

RATE_LIMIT_MARKERS = (
    "rate limit",
    "too many requests",
    "usage limit",
    "You've reached",
    "制限",
    "上限",
)


def generate_proof_token(seed: str, difficulty: str, user_agent: str = UA) -> str:
    screen = random.choice([3008, 4010, 6000]) * random.choice([1, 2, 4])
    parse_time = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    proof_token: list[Any] = [
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
    diff_len = len(difficulty or "")
    for i in range(500_000):
        proof_token[3] = i
        base = base64.b64encode(json.dumps(proof_token, separators=(",", ":")).encode()).decode()
        digest = hashlib.sha3_512((seed + base).encode()).hexdigest()
        if not difficulty or digest[:diff_len] <= difficulty:
            return "gAAAAAB" + base
    fallback = base64.b64encode(f'"{seed}"'.encode()).decode()
    return "gAAAAABwQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D" + fallback


class ChatGPTHttpSession:
    """Cookie-authenticated ChatGPT client without launching Chrome."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._session: requests.Session | None = None
        self._access_token: str | None = None
        self._model: str = "auto"
        self._impersonate: str = IMPERSONATE_CANDIDATES[0]
        # File IDs already seen in the shared image conversation (prevents re-downloading old gens).
        self._known_image_file_ids: set[str] = set()

    @staticmethod
    def _is_cloudflare_challenge(status_code: int, body: str, headers: Any = None) -> bool:
        if status_code != 403:
            return False
        hdrs = headers or {}
        if str(hdrs.get("cf-mitigated") or "").lower() == "challenge":
            return True
        low = (body or "")[:800].lower()
        return "just a moment" in low or "cf-browser-verification" in low or "challenge-platform" in low

    def start(self) -> None:
        cookies_list = load_cookies_from_file(self.settings.chatgpt_cookies_path)
        jar = {c["name"]: c["value"] for c in cookies_list if c.get("name") and c.get("value") is not None}
        if not jar:
            raise RuntimeError(f"No ChatGPT cookies at {self.settings.chatgpt_cookies_path}")

        last_error = "unknown"
        for impersonate in IMPERSONATE_CANDIDATES:
            try:
                session = requests.Session(impersonate=impersonate)
            except Exception as exc:  # noqa: BLE001
                last_error = f"impersonate={impersonate} unsupported: {exc}"
                logger.warning(last_error)
                continue
            session.cookies.update(jar)
            session.headers.update(
                {
                    "User-Agent": UA,
                    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                    "Referer": "https://chatgpt.com/",
                    "Origin": "https://chatgpt.com",
                }
            )
            self._session = session
            self._impersonate = impersonate
            try:
                self._refresh_access_token()
                self._pick_model()
                logger.info(
                    "ChatGPT HTTP session ready (model=%s impersonate=%s)",
                    self._model,
                    self._impersonate,
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning("HTTP session start failed with %s: %s", impersonate, exc)
                self._session = None
                self._access_token = None
                continue

        raise RuntimeError(
            f"ChatGPT HTTP session failed after trying {', '.join(IMPERSONATE_CANDIDATES)}: {last_error}. "
            "If Cloudflare is blocking, set CHATGPT_TRANSPORT=browser or refresh cookies via "
            "scripts/chatgpt_cookie_login.py"
        )

    def close(self) -> None:
        self._session = None
        self._access_token = None

    def _client(self) -> requests.Session:
        if not self._session:
            raise RuntimeError("ChatGPTHttpSession not started")
        return self._session

    def _auth_headers(self) -> dict[str, str]:
        if not self._access_token:
            self._refresh_access_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    def _refresh_access_token(self) -> None:
        client = self._client()
        r = client.get("https://chatgpt.com/api/auth/session", timeout=45)
        body = r.text or ""
        if r.status_code != 200:
            if self._is_cloudflare_challenge(r.status_code, body, r.headers):
                raise RuntimeError(
                    f"ChatGPT session blocked by Cloudflare (HTTP {r.status_code}, "
                    f"impersonate={self._impersonate}). Not a cookie expiry."
                )
            raise RuntimeError(
                f"ChatGPT session failed HTTP {r.status_code} "
                f"(cookies expired or auth rejected?). body={body[:180]!r}"
            )
        try:
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"ChatGPT session JSON parse failed: {exc}; body={body[:180]!r}") from exc
        token = data.get("accessToken")
        if not token:
            raise RuntimeError("ChatGPT accessToken missing — refresh secrets/chatgpt_cookies.json")
        self._access_token = token

    def _pick_model(self) -> None:
        try:
            r = self._client().get(
                "https://chatgpt.com/backend-api/models",
                headers=self._auth_headers(),
                timeout=45,
            )
            models = [m.get("slug") for m in (r.json().get("models") or []) if m.get("slug")]
            for cand in ("gpt-5-5", "gpt-5-6", "gpt-4o", "auto"):
                if cand in models:
                    self._model = cand
                    return
            if models:
                self._model = models[0]
        except Exception as exc:  # noqa: BLE001
            logger.warning("model list failed: %s", exc)
            self._model = "gpt-5-5"

    def _sentinel_headers(self) -> dict[str, str]:
        client = self._client()
        r = client.post(
            "https://chatgpt.com/backend-api/sentinel/chat-requirements",
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            json={"p": ""},
            timeout=45,
        )
        if r.status_code != 200:
            raise RuntimeError(f"chat-requirements failed: {r.status_code}")
        req = r.json()
        pow_info = req.get("proofofwork") or {}
        proof = ""
        if pow_info.get("required"):
            proof = generate_proof_token(str(pow_info.get("seed") or ""), str(pow_info.get("difficulty") or "0"))
        return {
            **self._auth_headers(),
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "Openai-Sentinel-Chat-Requirements-Token": str(req.get("token") or ""),
            "Openai-Sentinel-Proof-Token": proof,
            "Openai-Sentinel-Turnstile-Token": "",
        }

    def _parse_sse(self, response) -> dict[str, Any]:
        conversation_id = None
        assistant_text = ""
        image_assets: list[dict[str, Any]] = []
        rate_limited = False
        error_message = None
        for line in response.iter_lines():
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
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(ev.get("detail"), str):
                    error_message = ev["detail"]
                    low = error_message.lower()
                    if any(x in low for x in RATE_LIMIT_MARKERS):
                        rate_limited = True
                if ev.get("conversation_id"):
                    conversation_id = ev["conversation_id"]
                msg = ev.get("message") or {}
                content = msg.get("content") or {}
                role = (msg.get("author") or {}).get("role")
                for part in content.get("parts") or []:
                    if isinstance(part, str) and part.strip() and role == "assistant":
                        assistant_text = part
                    if isinstance(part, dict) and part.get("asset_pointer"):
                        image_assets.append(part)
                if role == "tool":
                    for part in content.get("parts") or []:
                        if isinstance(part, dict) and part.get("asset_pointer"):
                            image_assets.append(part)
            elif "rate" in line.lower() and "limit" in line.lower():
                rate_limited = True
        return {
            "conversation_id": conversation_id,
            "assistant_text": assistant_text,
            "image_assets": image_assets,
            "rate_limited": rate_limited,
            "error_message": error_message,
        }

    def _conversation_from_api(self, conversation_id: str) -> dict[str, Any]:
        r = self._client().get(
            f"https://chatgpt.com/backend-api/conversation/{conversation_id}",
            headers=self._auth_headers(),
            timeout=60,
        )
        if r.status_code != 200:
            return {}
        return r.json()

    @staticmethod
    def _file_id_from_pointer(pointer: str) -> str:
        return str(pointer or "").split("://")[-1].strip()

    def _extract_from_conversation(
        self, conversation: dict[str, Any], *, exclude_file_ids: set[str] | None = None
    ) -> tuple[str, list[dict[str, Any]]]:
        exclude = exclude_file_ids or set()
        text = ""
        images: list[dict[str, Any]] = []
        for node in (conversation.get("mapping") or {}).values():
            msg = (node or {}).get("message") or {}
            role = (msg.get("author") or {}).get("role")
            content = msg.get("content") or {}
            parts = content.get("parts") or []
            create_time = float(msg.get("create_time") or 0)
            message_id = str(msg.get("id") or "")
            if role == "assistant":
                for part in parts:
                    if isinstance(part, str) and part.strip():
                        text = part
            if role in {"assistant", "tool"}:
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    pointer = str(part.get("asset_pointer") or "")
                    fid = self._file_id_from_pointer(pointer)
                    if not fid or fid in exclude:
                        continue
                    size = int(part.get("size_bytes") or 0)
                    mime = str(part.get("mime_type") or "")
                    if size >= 80_000 or "image" in mime:
                        enriched = dict(part)
                        enriched["_create_time"] = create_time
                        enriched["_message_id"] = message_id
                        enriched["_file_id"] = fid
                        images.append(enriched)
        # Newest first
        images.sort(key=lambda a: float(a.get("_create_time") or 0), reverse=True)
        return text, images

    def snapshot_conversation_image_ids(self, conversation_id: str | None) -> set[str]:
        """Return all image file_ids currently in a conversation (+ session known set)."""
        known = set(self._known_image_file_ids)
        if not conversation_id:
            return known
        conv = self._conversation_from_api(conversation_id)
        _, imgs = self._extract_from_conversation(conv)
        for part in imgs:
            fid = str(part.get("_file_id") or self._file_id_from_pointer(part.get("asset_pointer") or ""))
            if fid:
                known.add(fid)
        return known

    @staticmethod
    def pick_newest_image_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Pick the newest generated asset (create_time), not the historically largest."""
        if not assets:
            return None
        timed = [a for a in assets if float(a.get("_create_time") or 0) > 0]
        if timed:
            return sorted(timed, key=lambda a: float(a.get("_create_time") or 0), reverse=True)[0]
        # Fallback: largest among remaining (should be rare for fresh-only lists)
        return sorted(assets, key=lambda a: int(a.get("size_bytes") or 0), reverse=True)[0]

    def send_text(
        self,
        prompt: str,
        *,
        timeout: int = 180,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        headers = self._sentinel_headers()
        parent_id = str(uuid.uuid4())
        if conversation_id:
            parent_id = self._parent_message_id(conversation_id) or parent_id
        payload: dict[str, Any] = {
            "action": "next",
            "messages": [
                {
                    "id": str(uuid.uuid4()),
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": [prompt]},
                    "metadata": {},
                }
            ],
            "parent_message_id": parent_id,
            "model": self._model,
            "timezone_offset_min": -540,
            "history_and_training_disabled": False,
            "conversation_mode": {"kind": "primary_assistant"},
            "websocket_request_id": str(uuid.uuid4()),
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        r = self._client().post(
            "https://chatgpt.com/backend-api/f/conversation",
            headers=headers,
            json=payload,
            timeout=timeout,
            stream=True,
        )
        if r.status_code != 200:
            return {
                "conversation_id": conversation_id,
                "assistant_text": "",
                "image_assets": [],
                "rate_limited": r.status_code == 429,
                "error_message": f"conversation HTTP {r.status_code}: {r.text[:300]}",
            }
        parsed = self._parse_sse(r)
        if conversation_id and not parsed.get("conversation_id"):
            parsed["conversation_id"] = conversation_id
        if not parsed["assistant_text"] and parsed["conversation_id"]:
            time.sleep(1.5)
            conv = self._conversation_from_api(parsed["conversation_id"])
            text, imgs = self._extract_from_conversation(conv)
            if text:
                parsed["assistant_text"] = text
            if imgs:
                parsed["image_assets"] = imgs
        return parsed

    def _parent_message_id(self, conversation_id: str) -> str | None:
        conv = self._conversation_from_api(conversation_id)
        current = conv.get("current_node")
        if isinstance(current, str) and current:
            return current
        mapping = conv.get("mapping") or {}
        # Prefer leaf assistant/user message
        for node_id, node in reversed(list(mapping.items())):
            msg = (node or {}).get("message") or {}
            if msg.get("id"):
                return str(msg["id"])
            if node_id:
                return str(node_id)
        return None

    def send_image_prompt(
        self,
        prompt: str,
        *,
        file_id: str,
        file_name: str,
        file_size: int,
        mime: str,
        timeout: int = 300,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        headers = self._sentinel_headers()
        parent_id = str(uuid.uuid4())
        if conversation_id:
            parent_id = self._parent_message_id(conversation_id) or parent_id
        # Prefer in-memory known IDs; full conversation snapshot only when empty (cold start).
        before_ids = set(self._known_image_file_ids)
        before_ids.add(file_id)
        if conversation_id and len(self._known_image_file_ids) < 2:
            before_ids |= self.snapshot_conversation_image_ids(conversation_id)
            before_ids.add(file_id)
        payload: dict[str, Any] = {
            "action": "next",
            "messages": [
                {
                    "id": str(uuid.uuid4()),
                    "author": {"role": "user"},
                    "content": {
                        "content_type": "multimodal_text",
                        "parts": [
                            {
                                "content_type": "image_asset_pointer",
                                "asset_pointer": f"file-service://{file_id}",
                                "size_bytes": file_size,
                                "width": 1024,
                                "height": 1024,
                            },
                            prompt,
                        ],
                    },
                    "metadata": {
                        "attachments": [
                            {
                                "id": file_id,
                                "name": file_name,
                                "size": file_size,
                                "mime_type": mime,
                                "width": 1024,
                                "height": 1024,
                            }
                        ]
                    },
                }
            ],
            "parent_message_id": parent_id,
            "model": self._model,
            "timezone_offset_min": -540,
            "history_and_training_disabled": False,
            "conversation_mode": {"kind": "primary_assistant"},
            "websocket_request_id": str(uuid.uuid4()),
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        r = self._client().post(
            "https://chatgpt.com/backend-api/f/conversation",
            headers=headers,
            json=payload,
            timeout=timeout,
            stream=True,
        )
        if r.status_code != 200:
            return {
                "conversation_id": conversation_id,
                "assistant_text": "",
                "image_assets": [],
                "rate_limited": r.status_code == 429,
                "error_message": f"image conversation HTTP {r.status_code}: {r.text[:300]}",
            }
        parsed = self._parse_sse(r)
        if conversation_id and not parsed.get("conversation_id"):
            parsed["conversation_id"] = conversation_id
        # Prefer assets that arrived in this SSE stream first (excluding pre-existing).
        stream_assets = [
            a
            for a in (parsed.get("image_assets") or [])
            if isinstance(a, dict)
            and self._file_id_from_pointer(a.get("asset_pointer") or "") not in before_ids
            and int(a.get("size_bytes") or 0) >= 50_000
        ]
        if stream_assets:
            parsed["image_assets"] = stream_assets
            return parsed

        # Image often arrives after stream; poll with short backoff (was 18×2s).
        delays = (0.5, 0.8, 1.0, 1.2, 1.5, 1.5, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0)
        for delay in delays:
            if not parsed.get("conversation_id"):
                break
            conv = self._conversation_from_api(parsed["conversation_id"])
            text, imgs = self._extract_from_conversation(conv, exclude_file_ids=before_ids)
            if text:
                parsed["assistant_text"] = text
            fresh = [i for i in imgs if int(i.get("size_bytes") or 0) >= 50_000]
            if fresh:
                parsed["image_assets"] = fresh
                logger.info(
                    "New image assets after prompt: %s",
                    [i.get("_file_id") for i in fresh[:5]],
                )
                break
            time.sleep(delay)
        else:
            # Timed out waiting for a NEW asset — do not fall back to old conversation images.
            if not any(
                self._file_id_from_pointer((a or {}).get("asset_pointer") or "") not in before_ids
                for a in (parsed.get("image_assets") or [])
                if isinstance(a, dict)
            ):
                parsed["image_assets"] = []
                parsed["error_message"] = (
                    "no NEW generated image asset (refused to reuse older conversation images)"
                )
        # Final filter in case stream left old assets in place.
        filtered = [
            a
            for a in (parsed.get("image_assets") or [])
            if isinstance(a, dict)
            and self._file_id_from_pointer(a.get("asset_pointer") or "") not in before_ids
        ]
        if filtered:
            parsed["image_assets"] = filtered
        elif parsed.get("image_assets"):
            parsed["image_assets"] = []
            parsed["error_message"] = (
                parsed.get("error_message")
                or "no NEW generated image asset (refused to reuse older conversation images)"
            )
        return parsed

    @staticmethod
    def conversation_id_from_url(url: str) -> str | None:
        m = re.search(r"/c/([0-9a-fA-F-]{20,})", url or "")
        return m.group(1) if m else None

    def generate_description_only(
        self,
        *,
        description_prompt: str,
        output_dir: Path,
        prompt_vars: dict | None = None,
        conversation_id: str | None = None,
        on_step: Callable[[str, str], None] | None = None,
    ) -> ChatGPTGenerationResult:
        def _step(name: str, message: str) -> None:
            if on_step:
                on_step(name, message)

        if not self._session:
            self.start()
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        vars_map = dict(prompt_vars or {})
        desc_prompt = safe_format(description_prompt, **vars_map)
        channel = conversation_id or self.conversation_id_from_url(
            self.settings.chatgpt_description_project_url
        )
        _step("description_generate", f"channel={channel or 'new'}")
        desc = self.send_text(
            desc_prompt,
            timeout=max(90, int(self.settings.chatgpt_wait_seconds)),
            conversation_id=channel,
        )
        if desc.get("rate_limited"):
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=None,
                rate_limited=True,
                error_message=desc.get("error_message") or "rate limited",
                prompt_sent=desc_prompt,
                step="description_generate",
            )
        description_text = (desc.get("assistant_text") or "").strip()
        if not description_text:
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=None,
                rate_limited=False,
                error_message=desc.get("error_message") or "empty description",
                prompt_sent=desc_prompt,
                step="description_generate",
            )
        description_path = output_dir / "description.txt"
        description_path.write_text(description_text, encoding="utf-8")
        (output_dir / "01_description.txt").write_text(description_text, encoding="utf-8")
        _step("description_saved", str(description_path))
        return ChatGPTGenerationResult(
            success=True,
            description_text=description_text,
            image_path=None,
            screenshot_path=None,
            rate_limited=False,
            prompt_sent=desc_prompt,
            description_path=description_path,
            step="desc_done",
        )

    def generate_image_only(
        self,
        *,
        image_prompt: str,
        output_dir: Path,
        source_product_image: Path,
        prompt_vars: dict | None = None,
        conversation_id: str | None = None,
        on_step: Callable[[str, str], None] | None = None,
    ) -> ChatGPTGenerationResult:
        def _step(name: str, message: str) -> None:
            if on_step:
                on_step(name, message)

        if not self._session:
            self.start()
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        src = Path(source_product_image)
        if not src.exists():
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=None,
                rate_limited=False,
                error_message="source product image required",
                source_image_path=src,
                step="image_generate",
            )
        vars_map = dict(prompt_vars or {})
        img_prompt = safe_format(image_prompt, **vars_map)
        channel = conversation_id or self.conversation_id_from_url(self.settings.chatgpt_image_project_url)
        _step("image_upload", src.name)
        file_id = self.upload_image(src)
        mime = mimetypes.guess_type(str(src))[0] or "application/octet-stream"
        _step("image_generate", f"channel={channel or 'new'}")
        img_res = self.send_image_prompt(
            img_prompt,
            file_id=file_id,
            file_name=src.name,
            file_size=src.stat().st_size,
            mime=mime,
            timeout=max(120, int(self.settings.chatgpt_wait_seconds)),
            conversation_id=channel,
        )
        if img_res.get("rate_limited"):
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=None,
                rate_limited=True,
                error_message=img_res.get("error_message") or "rate limited on image",
                prompt_sent=img_prompt,
                source_image_path=src,
                step="image_generate",
            )
        assets = img_res.get("image_assets") or []
        conv_id = img_res.get("conversation_id")
        if not assets or not conv_id:
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=None,
                rate_limited=False,
                error_message=img_res.get("error_message") or "no generated image asset",
                prompt_sent=img_prompt,
                source_image_path=src,
                step="image_generate",
            )
        chosen = self.pick_newest_image_asset(assets)
        if not chosen:
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=None,
                rate_limited=False,
                error_message="no generated image asset to download",
                prompt_sent=img_prompt,
                source_image_path=src,
                step="image_generate",
            )
        gen_file_id = str(
            chosen.get("_file_id") or self._file_id_from_pointer(chosen.get("asset_pointer") or "")
        )
        image_path = output_dir / "0.png"
        _step(
            "image_download",
            f"{gen_file_id} (create_time={chosen.get('_create_time')}, size={chosen.get('size_bytes')})",
        )
        self.download_file(gen_file_id, conv_id, image_path)
        self._known_image_file_ids.add(gen_file_id)
        self._known_image_file_ids.add(file_id)
        return ChatGPTGenerationResult(
            success=True,
            description_text=None,
            image_path=image_path,
            screenshot_path=None,
            rate_limited=False,
            prompt_sent=img_prompt,
            source_image_path=src,
            step="done",
        )

    def upload_image(self, path: Path) -> str:
        path = Path(path)
        upload_path = path
        upload_bytes: bytes | None = None
        # Shrink huge source files before upload (faster + fewer timeouts).
        try:
            from PIL import Image
            import io

            raw = path.read_bytes()
            if len(raw) > 900_000:
                img = Image.open(io.BytesIO(raw))
                if img.mode in {"RGBA", "LA", "P"}:
                    img = img.convert("RGBA")
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                    img = bg
                else:
                    img = img.convert("RGB")
                w, h = img.size
                scale = min(1.0, 1280.0 / float(max(w, h) or 1))
                if scale < 1.0:
                    img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85, optimize=True)
                upload_bytes = buf.getvalue()
                mime = "image/jpeg"
                file_name = f"{path.stem}.jpg"
                file_size = len(upload_bytes)
            else:
                mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
                file_name = path.name
                file_size = path.stat().st_size
        except Exception:  # noqa: BLE001
            mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            file_name = path.name
            file_size = path.stat().st_size
            upload_bytes = None

        meta = {
            "file_name": file_name,
            "file_size": file_size,
            "use_case": "multimodal",
            "timezone_offset_min": -540,
        }
        r = self._client().post(
            "https://chatgpt.com/backend-api/files",
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            json=meta,
            timeout=45,
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"files create failed: {r.status_code} {r.text[:200]}")
        info = r.json()
        file_id = info.get("file_id")
        upload_url = info.get("upload_url")
        if not file_id or not upload_url:
            raise RuntimeError(f"files create missing fields: {info}")
        blob = upload_bytes if upload_bytes is not None else upload_path.read_bytes()
        put = requests.put(
            upload_url,
            data=blob,
            headers={"Content-Type": mime, "x-ms-blob-type": "BlockBlob"},
            timeout=90,
            impersonate=self._impersonate,
        )
        if put.status_code not in (200, 201):
            raise RuntimeError(f"blob upload failed: {put.status_code}")
        done = self._client().post(
            f"https://chatgpt.com/backend-api/files/{file_id}/uploaded",
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            json={},
            timeout=45,
        )
        if done.status_code != 200:
            raise RuntimeError(f"files uploaded failed: {done.status_code} {done.text[:200]}")
        return str(file_id)

    def download_file(self, file_id: str, conversation_id: str, dest: Path) -> Path:
        file_id = file_id.split("://")[-1]
        meta = self._client().get(
            f"https://chatgpt.com/backend-api/files/download/{file_id}"
            f"?conversation_id={conversation_id}&inline=false",
            headers=self._auth_headers(),
            timeout=60,
        )
        if meta.status_code != 200:
            raise RuntimeError(f"download meta failed: {meta.status_code} {meta.text[:200]}")
        url = meta.json().get("download_url")
        if not url:
            raise RuntimeError("download_url missing")
        if url.startswith("/"):
            url = "https://chatgpt.com" + url
        r = self._client().get(url, headers=self._auth_headers(), timeout=120)
        if r.status_code != 200 or not r.content or r.content[:8] == b'{"status':
            raise RuntimeError(f"image download failed: {r.status_code} len={len(r.content)}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest

    def generate_for_brand(
        self,
        *,
        brand_name: str,
        site_name: str,
        description_prompt: str,
        image_prompt: str,
        output_dir: Path,
        source_product_image: Path | None = None,
        on_step: Callable[[str, str], None] | None = None,
        prompt_vars: dict | None = None,
    ) -> ChatGPTGenerationResult:
        def _step(name: str, message: str) -> None:
            if on_step:
                on_step(name, message)

        if not self._session:
            self.start()

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        vars_map = {
            "brand_name": brand_name,
            "site_name": site_name,
            "product_name": brand_name,
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
        }
        if prompt_vars:
            vars_map.update({k: ("" if v is None else str(v)) for k, v in prompt_vars.items()})
        desc_prompt = safe_format(description_prompt, **vars_map)
        img_prompt = safe_format(image_prompt, **vars_map)

        desc_channel = self.conversation_id_from_url(self.settings.chatgpt_description_project_url)
        img_channel = self.conversation_id_from_url(self.settings.chatgpt_image_project_url)
        _step("description_generate", f"HTTP description for {brand_name}")
        desc = self.send_text(
            desc_prompt,
            timeout=max(90, int(self.settings.chatgpt_wait_seconds)),
            conversation_id=desc_channel,
        )
        if desc.get("rate_limited"):
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=None,
                rate_limited=True,
                error_message=desc.get("error_message") or "rate limited",
                prompt_sent=desc_prompt,
                source_image_path=source_product_image,
                step="description_generate",
            )
        description_text = (desc.get("assistant_text") or "").strip()
        if not description_text:
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=None,
                rate_limited=False,
                error_message=desc.get("error_message") or "empty description",
                prompt_sent=desc_prompt,
                source_image_path=source_product_image,
                step="description_generate",
            )
        description_path = output_dir / "description.txt"
        description_path.write_text(description_text, encoding="utf-8")
        (output_dir / "01_description.txt").write_text(description_text, encoding="utf-8")
        _step("description_saved", str(description_path))

        if source_product_image is None or not Path(source_product_image).exists():
            return ChatGPTGenerationResult(
                success=False,
                description_text=description_text,
                image_path=None,
                screenshot_path=None,
                rate_limited=False,
                error_message="source product image required",
                prompt_sent=img_prompt,
                source_image_path=source_product_image,
                description_path=description_path,
                step="image_generate",
            )

        src = Path(source_product_image)
        _step("image_upload", src.name)
        file_id = self.upload_image(src)
        mime = mimetypes.guess_type(str(src))[0] or "application/octet-stream"
        _step("image_generate", f"HTTP image for {brand_name}")
        img_res = self.send_image_prompt(
            img_prompt,
            file_id=file_id,
            file_name=src.name,
            file_size=src.stat().st_size,
            mime=mime,
            timeout=max(120, int(self.settings.chatgpt_wait_seconds)),
            conversation_id=img_channel,
        )
        if img_res.get("rate_limited"):
            return ChatGPTGenerationResult(
                success=False,
                description_text=description_text,
                image_path=None,
                screenshot_path=None,
                rate_limited=True,
                error_message=img_res.get("error_message") or "rate limited on image",
                prompt_sent=img_prompt,
                source_image_path=src,
                description_path=description_path,
                step="image_generate",
            )
        assets = img_res.get("image_assets") or []
        conv_id = img_res.get("conversation_id")
        if not assets or not conv_id:
            return ChatGPTGenerationResult(
                success=False,
                description_text=description_text,
                image_path=None,
                screenshot_path=None,
                rate_limited=False,
                error_message=img_res.get("error_message") or "no generated image asset",
                prompt_sent=img_prompt,
                source_image_path=src,
                description_path=description_path,
                step="image_generate",
            )
        chosen = self.pick_newest_image_asset(assets)
        if not chosen:
            return ChatGPTGenerationResult(
                success=False,
                description_text=description_text,
                image_path=None,
                screenshot_path=None,
                rate_limited=False,
                error_message="no generated image asset to download",
                prompt_sent=img_prompt,
                source_image_path=src,
                description_path=description_path,
                step="image_generate",
            )
        gen_file_id = str(
            chosen.get("_file_id") or self._file_id_from_pointer(chosen.get("asset_pointer") or "")
        )
        image_path = output_dir / "0.png"
        _step(
            "image_download",
            f"{gen_file_id} (create_time={chosen.get('_create_time')}, size={chosen.get('size_bytes')})",
        )
        self.download_file(gen_file_id, conv_id, image_path)
        self._known_image_file_ids.add(gen_file_id)
        self._known_image_file_ids.add(file_id)
        return ChatGPTGenerationResult(
            success=True,
            description_text=description_text,
            image_path=image_path,
            screenshot_path=None,
            rate_limited=False,
            prompt_sent=img_prompt,
            source_image_path=src,
            description_path=description_path,
            step="done",
        )
