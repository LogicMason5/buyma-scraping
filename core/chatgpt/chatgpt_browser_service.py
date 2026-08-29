from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from playwright.sync_api import BrowserContext, Page

from core.config import get_settings
from core.chatgpt.chatgpt_cookie_service import apply_cookies, save_cookies_to_file
from core.utils.chrome_profile import prepare_chrome_profile
from core.utils.playwright_runtime import acquire_playwright, release_playwright

logger = logging.getLogger(__name__)

RATE_LIMIT_PATTERNS = [
    r"rate limit",
    r"too many requests",
    r"try again later",
    r"usage limit",
    r"limit reached",
    r"You've reached",
    r"制限",
    r"上限",
]


@dataclass
class ChatGPTGenerationResult:
    success: bool
    description_text: str | None
    image_path: Path | None
    screenshot_path: Path | None
    rate_limited: bool
    error_message: str | None = None
    prompt_sent: str | None = None
    source_image_path: Path | None = None
    description_path: Path | None = None
    step: str | None = None


class ChatGPTBrowserSession:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._playwright = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        # Avoid re-saving the same CDN/src across products in one batch.
        self._downloaded_image_srcs: set[str] = set()

    def start(self) -> None:
        profile_path = Path(self.settings.chatgpt_profile_path)
        prepare_chrome_profile(profile_path)
        self._playwright = acquire_playwright()
        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            channel="chrome",
            headless=False,
            accept_downloads=True,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            viewport=None,
            ignore_default_args=["--enable-automation"],
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        # Cookie-only auth: never auto-fill email/password.
        applied = apply_cookies(self.context, self.settings.chatgpt_cookies_path)
        if applied:
            logger.info("Applied %s ChatGPT cookies from local secrets file.", applied)
        self.page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
        self.page.wait_for_timeout(2500)
        if not self._is_logged_in():
            # Retry once after cookie settle.
            self.page.reload(wait_until="domcontentloaded")
            self.page.wait_for_timeout(2500)
        ok = self.ensure_logged_in(timeout_seconds=min(60, self.settings.chatgpt_login_timeout_seconds))
        if not ok:
            logger.warning(
                "ChatGPT cookie session not ready. Update secrets/chatgpt_cookies.json if needed."
            )
        else:
            # Prove composer works before pipeline starts generating.
            try:
                self.wait_for_composer(timeout_seconds=20)
            except RuntimeError as exc:
                logger.warning("ChatGPT composer not ready after login: %s", exc)
    def close(self) -> None:
        if self.context:
            try:
                self.context.close()
            except Exception:  # noqa: BLE001
                pass
        if self._playwright:
            release_playwright()
        self.context = None
        self.page = None
        self._playwright = None

    def _composer_locator(self):
        assert self.page is not None
        selectors = [
            "div#prompt-textarea",
            "div[contenteditable='true']#prompt-textarea",
            "textarea#prompt-textarea",
            "div[contenteditable='true'][data-placeholder]",
            "div.ProseMirror[contenteditable='true']",
            "textarea[placeholder*='Message']",
            "textarea[placeholder*='メッセージ']",
        ]
        for selector in selectors:
            locator = self.page.locator(selector).first
            try:
                if locator.count() > 0 and locator.is_visible():
                    return locator
            except Exception:  # noqa: BLE001
                continue
        return None

    def _is_logged_in(self) -> bool:
        """True only when a visible composer exists. Never trust bare /g/ project URLs."""
        assert self.page is not None
        url = self.page.url.lower()
        if "auth" in url or "/login" in url or "signin" in url:
            return False
        try:
            body = self.page.inner_text("body").lower()
        except Exception:  # noqa: BLE001
            body = ""
        if "log in" in body or "sign up" in body or "create an account" in body:
            # Still allow if composer is present (sidebar may mention login elsewhere).
            pass
        return self._composer_locator() is not None

    def _assist_login(self) -> None:
        """Disabled: ChatGPT auth is cookie-only. Never fill email/password."""
        logger.info("ChatGPT password login assist is disabled. Using cookies only.")

    def ensure_logged_in(self, timeout_seconds: int = 300) -> bool:
        if not self.page:
            self.start()
        assert self.page is not None

        if self._is_logged_in():
            self.save_session_cookies()
            return True

        # Re-apply cookies and reopen chatgpt.com without credentials.
        if self.context:
            apply_cookies(self.context, self.settings.chatgpt_cookies_path)
        self.page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
        self.page.wait_for_timeout(2500)

        deadline = time.time() + timeout_seconds
        logger.warning(
            "Waiting for ChatGPT cookie session (%ss). No email/password will be entered.",
            timeout_seconds,
        )
        while time.time() < deadline:
            if self._is_logged_in():
                self.save_session_cookies()
                return True
            self.page.wait_for_timeout(2000)
        return False

    def save_session_cookies(self) -> dict:
        assert self.context is not None
        result = save_cookies_to_file(self.context, self.settings.chatgpt_cookies_path)
        logger.info(
            "Saved ChatGPT cookies locally: count=%s path=%s skipped=%s",
            result.get("cookie_count"),
            result.get("path"),
            result.get("skipped"),
        )
        return result

    def ensure_ready(self) -> bool:
        return self.ensure_logged_in(timeout_seconds=60)

    @staticmethod
    def _is_conversation_url(url: str) -> bool:
        """True for fixed chat links (.../c/<id>), False for project hubs that need New chat."""
        return "/c/" in (url or "")

    def _click_new_chat_if_needed(self) -> None:
        assert self.page is not None
        # Fixed conversation links must stay on the same chat — never open a new channel.
        if self._is_conversation_url(self.page.url):
            return
        if self._composer_locator() is not None:
            return
        labels = [
            r"New chat",
            r"新しいチャット",
            r"New chat in",
            r"\+ New chat",
        ]
        for pattern in labels:
            btn = self.page.get_by_role("button", name=re.compile(pattern, re.I))
            if btn.count() == 0:
                btn = self.page.get_by_role("link", name=re.compile(pattern, re.I))
            if btn.count() > 0:
                try:
                    btn.first.click(timeout=4000)
                    self.page.wait_for_timeout(1500)
                    if self._composer_locator() is not None:
                        return
                except Exception:  # noqa: BLE001
                    continue
        # Text fallback (project empty state).
        for text in ["New chat", "新しいチャット", "+ New chat"]:
            loc = self.page.locator(f"text={text}").first
            try:
                if loc.count() > 0:
                    loc.click(timeout=3000)
                    self.page.wait_for_timeout(1500)
                    if self._composer_locator() is not None:
                        return
            except Exception:  # noqa: BLE001
                continue

    def wait_for_composer(self, timeout_seconds: int = 45, *, allow_new_chat: bool = True) -> None:
        assert self.page is not None
        deadline = time.time() + timeout_seconds
        last_error = "composer not found"
        while time.time() < deadline:
            if allow_new_chat:
                self._click_new_chat_if_needed()
            if self._composer_locator() is not None:
                return
            self.page.wait_for_timeout(1000)
        try:
            shot = Path(self.settings.production_root) / "_debug" / f"chatgpt_no_composer_{int(time.time())}.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(shot), full_page=False)
            last_error = f"composer not found (screenshot={shot})"
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(
            f"ChatGPT prompt input not found within {timeout_seconds}s. "
            f"Update secrets/chatgpt_cookies.json and reopen the conversation/project. ({last_error})"
        )

    def open_project(self, project_url: str) -> None:
        """Open a fixed conversation URL or a project hub. Conversation links reuse the same chat."""
        assert self.page is not None
        allow_new_chat = not self._is_conversation_url(project_url)
        self.page.goto(project_url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)
        if allow_new_chat:
            self._click_new_chat_if_needed()
        try:
            self.wait_for_composer(timeout_seconds=40, allow_new_chat=allow_new_chat)
        except RuntimeError:
            # Re-apply cookies once and retry open. Do not rewrite cookie files here.
            if self.context:
                apply_cookies(self.context, self.settings.chatgpt_cookies_path)
            self.page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)
            if not self.ensure_logged_in(timeout_seconds=45):
                raise RuntimeError("ChatGPT is not logged in with cookies. Refresh secrets/chatgpt_cookies.json.")
            self.page.goto(project_url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)
            if allow_new_chat:
                self._click_new_chat_if_needed()
            self.wait_for_composer(timeout_seconds=40, allow_new_chat=allow_new_chat)

    def _composer(self):
        loc = self._composer_locator()
        if loc is not None:
            return loc
        self._click_new_chat_if_needed()
        loc = self._composer_locator()
        if loc is not None:
            return loc
        raise RuntimeError("ChatGPT prompt input not found. Keep the ChatGPT project tab visible and logged in.")
    def _send_button(self):
        assert self.page is not None
        selectors = [
            "button[data-testid='send-button']",
            "button[aria-label*='Send']",
            "button:has-text('Send')",
        ]
        for selector in selectors:
            locator = self.page.locator(selector).first
            if locator.count() > 0:
                return locator
        return None

    def attach_image(self, image_path: Path) -> None:
        assert self.page is not None
        if not image_path.exists():
            raise FileNotFoundError(f"Source product image not found: {image_path}")
        self.wait_for_composer(timeout_seconds=30)

        file_inputs = self.page.locator("input[type='file']")
        if file_inputs.count() > 0:
            file_inputs.last.set_input_files(str(image_path))
            self.page.wait_for_timeout(2500)
            return

        # Open attachment menu then inject file chooser.
        for label in ["Attach", "アップロード", "Add photos", "Add photos and files", "添付", "写真"]:
            btn = self.page.get_by_role("button", name=re.compile(label, re.I))
            if btn.count() > 0:
                try:
                    with self.page.expect_file_chooser(timeout=8000) as fc_info:
                        btn.first.click()
                    chooser = fc_info.value
                    chooser.set_files(str(image_path))
                    self.page.wait_for_timeout(2500)
                    return
                except Exception:  # noqa: BLE001
                    continue

        # Fallback: create hidden input for upload.
        handle = self.page.evaluate_handle(
            """() => {
                const input = document.createElement('input');
                input.type = 'file';
                input.accept = 'image/*';
                input.style.display = 'none';
                document.body.appendChild(input);
                return input;
            }"""
        )
        handle.as_element().set_input_files(str(image_path))  # type: ignore[union-attr]
        self.page.wait_for_timeout(2500)
    def _set_composer_text(self, prompt: str) -> None:
        assert self.page is not None
        self.wait_for_composer(timeout_seconds=30)
        composer = self._composer()
        try:
            composer.click(timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        self.page.wait_for_timeout(200)

        # Clear + insert via DOM (ProseMirror-safe). Avoid locator.type(delay=...) which can hang.
        ok = False
        try:
            ok = bool(
                self.page.evaluate(
                    """(text) => {
                      const el = document.querySelector('#prompt-textarea, div.ProseMirror[contenteditable=\"true\"], div[contenteditable=\"true\"]');
                      if (!el) return false;
                      el.focus();
                      // Clear
                      if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                        el.value = '';
                        el.value = text;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        return true;
                      }
                      try {
                        document.execCommand('selectAll', false, null);
                        document.execCommand('delete', false, null);
                        const inserted = document.execCommand('insertText', false, text);
                        if (inserted) return true;
                      } catch (e) {}
                      el.textContent = text;
                      el.dispatchEvent(new InputEvent('input', { bubbles: true, data: text, inputType: 'insertText' }));
                      return (el.innerText || el.textContent || '').trim().length > 0;
                    }""",
                    prompt,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("DOM composer insert failed: %s", exc)
            ok = False

        if not ok:
            try:
                self.page.keyboard.press("Control+A")
                self.page.keyboard.press("Backspace")
                self.page.keyboard.insert_text(prompt)
                ok = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("keyboard insert_text failed: %s", exc)

        if not ok:
            raise RuntimeError("Failed to enter prompt into ChatGPT composer.")
        self.page.wait_for_timeout(300)
    def send_prompt(
        self,
        prompt: str,
        *,
        wait_seconds: int | None = None,
        image_path: Path | None = None,
    ) -> tuple[str, bool]:
        assert self.page is not None
        wait_seconds = wait_seconds or self.settings.chatgpt_wait_seconds
        if image_path is not None:
            self.attach_image(image_path)
            self.page.wait_for_timeout(2000)
        self._set_composer_text(prompt)
        self.page.wait_for_timeout(400)
        if self.settings.chatgpt_auto_send:
            button = self._send_button()
            if button:
                try:
                    button.click(timeout=5000)
                except Exception:  # noqa: BLE001
                    self.page.keyboard.press("Enter")
            else:
                self.page.keyboard.press("Enter")
        # Short settle; full wait happens in wait_for_response_complete.
        self.page.wait_for_timeout(min(5000, max(1500, wait_seconds * 200)))
        body_text = self.page.inner_text("body")
        rate_limited = any(re.search(p, body_text, re.IGNORECASE) for p in RATE_LIMIT_PATTERNS)
        return body_text, rate_limited
    def latest_assistant_text(self) -> str:
        assert self.page is not None
        selectors = [
            "[data-message-author-role='assistant']",
            "div.markdown",
            "article",
        ]
        for selector in selectors:
            nodes = self.page.locator(selector)
            count = nodes.count()
            if count > 0:
                return nodes.nth(count - 1).inner_text().strip()
        return ""

    def copy_latest_description(self) -> str:
        """Read latest assistant text; try Copy button + clipboard as enhancement."""
        assert self.page is not None
        description = self.latest_assistant_text()

        # Prefer explicit Copy button under the latest assistant turn (best-effort, never hang).
        try:
            assistants = self.page.locator("[data-message-author-role='assistant']")
            if assistants.count() > 0:
                latest = assistants.nth(assistants.count() - 1)
                for selector in [
                    "button[aria-label='Copy']",
                    "button[aria-label*='Copy']",
                    "button[data-testid='copy-turn-action-button']",
                    "button:has-text('Copy')",
                ]:
                    btn = latest.locator(selector)
                    if btn.count() == 0:
                        btn = latest.locator("xpath=ancestor::*[1]").locator(selector)
                    if btn.count() > 0:
                        btn.first.click(force=True, timeout=3000)
                        self.page.wait_for_timeout(400)
                        break
        except Exception as exc:  # noqa: BLE001
            logger.info("Copy button click skipped: %s", exc)

        try:
            clipboard = self.page.evaluate(
                """async () => {
                  try {
                    return await Promise.race([
                      navigator.clipboard.readText(),
                      new Promise((_, reject) => setTimeout(() => reject(new Error('clipboard timeout')), 1500))
                    ]);
                  } catch (e) { return ''; }
                }"""
            )
            if clipboard and str(clipboard).strip():
                description = str(clipboard).strip()
        except Exception:  # noqa: BLE001
            logger.info("Clipboard read unavailable; using DOM assistant text.")

        return (description or "").strip()

    def wait_for_response_complete(self, timeout_seconds: int | None = None) -> None:
        assert self.page is not None
        timeout_seconds = timeout_seconds or self.settings.chatgpt_wait_seconds
        deadline = time.time() + timeout_seconds
        stable_hits = 0
        last_text = ""
        saw_any = False
        while time.time() < deadline:
            # Still streaming if stop button is visible.
            try:
                stop = self.page.locator("button[aria-label*='Stop'], button[data-testid='stop-button']")
                if stop.count() > 0 and stop.first.is_visible():
                    stable_hits = 0
                    saw_any = True
                    self.page.wait_for_timeout(1000)
                    continue
            except Exception:  # noqa: BLE001
                pass
            text = self.latest_assistant_text()
            if text:
                saw_any = True
            if text and text == last_text:
                stable_hits += 1
                if stable_hits >= 3:
                    return
            else:
                stable_hits = 0
                last_text = text
            self.page.wait_for_timeout(1000)
        if not saw_any:
            logger.warning("No assistant response detected within %ss", timeout_seconds)
    def save_description_file(self, production_dir: Path, description_text: str) -> Path:
        """Save only the generated description result as TXT."""
        production_dir.mkdir(parents=True, exist_ok=True)
        description_path = production_dir / "description.txt"
        description_path.write_text(description_text.strip() + "\n", encoding="utf-8")
        # Compatibility alias used by export/listing helpers.
        (production_dir / "01_description.txt").write_text(description_text.strip() + "\n", encoding="utf-8")
        return description_path

    def _generated_image_candidates(self) -> list[tuple]:
        """Return latest large assistant/CDN images (locator, src), newest first."""
        assert self.page is not None
        preferred = self.page.locator(
            "img[src*='oaiusercontent'], img[src*='oaidalle'], img[src*='dalle'], "
            "img[src*='images.openai'], [data-message-author-role='assistant'] img"
        )
        candidates: list[tuple] = []
        count = preferred.count()
        for i in range(count - 1, -1, -1):
            loc = preferred.nth(i)
            src = loc.get_attribute("src") or ""
            if not src or src.startswith("data:image/svg"):
                continue
            if "avatar" in src.lower() or "profile" in src.lower():
                continue
            candidates.append((loc, src))

        if candidates:
            return candidates

        images = self.page.locator("img")
        count = images.count()
        for i in range(count - 1, -1, -1):
            loc = images.nth(i)
            src = loc.get_attribute("src") or ""
            if not src or src.startswith("data:image/svg"):
                continue
            if any(x in src.lower() for x in ["avatar", "profile", "logo", "icon"]):
                continue
            try:
                box = loc.bounding_box()
                if box and (box["width"] < 160 or box["height"] < 160):
                    continue
            except Exception:  # noqa: BLE001
                pass
            candidates.append((loc, src))
        return candidates

    def _save_bytes_if_image(self, data: bytes, target_path: Path) -> Path | None:
        if len(data) < 2000:
            return None
        # Reject HTML/error pages mistaken for images.
        head = data[:32].lstrip()
        if head.startswith(b"<") or head.startswith(b"{") or head.lower().startswith(b"<!doctype"):
            return None
        target_path.write_bytes(data)
        return target_path

    def _download_via_chatgpt_button(self, img_locator, target_path: Path) -> Path | None:
        """Click ChatGPT's Download control and save the raw file as target_path."""
        assert self.page is not None
        try:
            img_locator.scroll_into_view_if_needed(timeout=5000)
            img_locator.hover(timeout=5000)
            self.page.wait_for_timeout(400)
        except Exception:  # noqa: BLE001
            pass

        # Some layouts hide Download until the image is focused / expanded.
        try:
            img_locator.click(timeout=3000)
            self.page.wait_for_timeout(600)
        except Exception:  # noqa: BLE001
            pass

        # Scope to the image card / message so we don't hit unrelated downloads.
        scopes = []
        try:
            card = img_locator.locator(
                "xpath=ancestor::*[contains(@class,'group') or @data-message-author-role][1]"
            )
            if card.count():
                scopes.append(card.first)
        except Exception:  # noqa: BLE001
            pass
        # Dialog / lightbox when image was expanded.
        try:
            dialog = self.page.locator("[role='dialog'], [data-state='open']").first
            if dialog.count() and dialog.is_visible():
                scopes.insert(0, dialog)
        except Exception:  # noqa: BLE001
            pass
        scopes.append(self.page.locator("body"))

        button_queries = [
            lambda root: root.get_by_role("button", name=re.compile(r"download|ダウンロード", re.I)),
            lambda root: root.get_by_role("link", name=re.compile(r"download|ダウンロード", re.I)),
            lambda root: root.locator(
                "button[aria-label*='Download'], button[aria-label*='download'], "
                "button[aria-label*='ダウンロード'], a[aria-label*='Download'], "
                "a[aria-label*='download'], a[aria-label*='ダウンロード'], "
                "button[data-testid*='download'], a[download]"
            ),
            lambda root: root.locator("button:has-text('Download'), button:has-text('ダウンロード')"),
            # Icon-only download near Edit (title/tooltip attributes).
            lambda root: root.locator(
                "[title*='Download'], [title*='download'], [title*='ダウンロード'], "
                "[data-tooltip*='Download'], [data-tooltip*='download'], [data-tooltip*='ダウンロード']"
            ),
        ]

        for root in scopes:
            for query in button_queries:
                try:
                    btn = query(root)
                except Exception:  # noqa: BLE001
                    continue
                n = min(btn.count(), 8)
                for i in range(n):
                    candidate = btn.nth(i)
                    try:
                        if not candidate.is_visible():
                            continue
                    except Exception:  # noqa: BLE001
                        continue
                    try:
                        with self.page.expect_download(timeout=45000) as download_info:
                            candidate.click(timeout=5000)
                        download = download_info.value
                        download.save_as(str(target_path))
                        if target_path.exists() and target_path.stat().st_size > 2000:
                            logger.info(
                                "Saved ChatGPT download (%s) -> %s",
                                download.suggested_filename,
                                target_path,
                            )
                            return target_path
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Download button click failed: %s", exc)
                        continue
        return None

    def _download_via_authenticated_src(self, src: str, target_path: Path) -> Path | None:
        """Fetch img src with the logged-in browser session (cookies / auth)."""
        assert self.page is not None
        if not src:
            return None
        if src.startswith("data:image/"):
            try:
                import base64

                _, encoded = src.split(",", 1)
                return self._save_bytes_if_image(base64.b64decode(encoded), target_path)
            except Exception:  # noqa: BLE001
                return None
        try:
            resp = self.page.request.get(src, timeout=45000)
            if resp.ok:
                saved = self._save_bytes_if_image(resp.body(), target_path)
                if saved:
                    logger.info("Saved authenticated image src -> %s", target_path)
                    return saved
        except Exception as exc:  # noqa: BLE001
            logger.debug("Authenticated src fetch failed: %s", exc)
        # Fallback without browser cookies (rarely works for oaiusercontent).
        if src.startswith("http"):
            try:
                with httpx.Client(timeout=40.0, follow_redirects=True) as client:
                    resp = client.get(src)
                    if resp.status_code == 200:
                        return self._save_bytes_if_image(resp.content, target_path)
            except Exception:  # noqa: BLE001
                pass
        return None

    def capture_generated_image(self, target_path: Path) -> Path | None:
        """
        Save the raw generated model image (no ChatGPT chrome).

        Prefer authenticated CDN fetch of the newest assistant image, then Download button.
        Never use element/viewport screenshots for production 0.png — those include
        Edit/Share overlays and rounded UI frames.
        Never reuse a src already downloaded earlier in this session.
        """
        assert self.page is not None
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            target_path.unlink()

        candidates = self._generated_image_candidates()
        if not candidates:
            debug = target_path.parent / "chatgpt_image_missing.png"
            self.page.screenshot(path=str(debug), full_page=False)
            logger.warning("No generated image found; debug shot=%s", debug)
            return None

        # Drop already-downloaded srcs so every product gets the latest unique generation.
        fresh = [(loc, src) for loc, src in candidates if src and src not in self._downloaded_image_srcs]
        if not fresh:
            logger.warning(
                "All candidate image srcs were already downloaded this session (%s). "
                "Waiting briefly for a newer image…",
                len(self._downloaded_image_srcs),
            )
            self.page.wait_for_timeout(4000)
            candidates = self._generated_image_candidates()
            fresh = [(loc, src) for loc, src in candidates if src and src not in self._downloaded_image_srcs]
        if not fresh:
            debug = target_path.parent / "chatgpt_image_stale.png"
            self.page.screenshot(path=str(debug), full_page=False)
            logger.error("No NEW generated image src found; refusing to reuse old image. debug=%s", debug)
            return None

        for locator, src in fresh:
            # 1) Authenticated GET of the <img src> first (src is unique per generation).
            saved = self._download_via_authenticated_src(src, target_path)
            if saved:
                self._downloaded_image_srcs.add(src)
                return saved

            # 2) ChatGPT Download button → raw image file.
            saved = self._download_via_chatgpt_button(locator, target_path)
            if saved:
                self._downloaded_image_srcs.add(src)
                return saved

        debug = target_path.parent / "chatgpt_image_capture_failed.png"
        self.page.screenshot(path=str(debug), full_page=False)
        logger.error(
            "Could not download clean image (UI screenshot avoided). debug=%s",
            debug,
        )
        return None

    def generate_description_only(
        self,
        *,
        description_prompt: str,
        output_dir: Path,
        prompt_vars: dict | None = None,
        on_step=None,
        open_channel: bool = False,
    ) -> ChatGPTGenerationResult:
        """Generate description only. Caller should open the description channel once for a batch."""
        def _step(name: str, message: str) -> None:
            if on_step:
                on_step(name, message)

        from core.prompts import safe_format

        if not self.ensure_ready():
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=None,
                rate_limited=False,
                error_message="ChatGPT login required for your account. Complete login in the dedicated Chrome window.",
                step="login",
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        vars_map = dict(prompt_vars or {})
        desc_prompt = safe_format(description_prompt, **vars_map)
        if open_channel:
            self.open_project(self.settings.chatgpt_description_project_url)
        _step("description_generate", "Generating description")
        body, rate_limited = self.send_prompt(desc_prompt, wait_seconds=self.settings.chatgpt_wait_seconds)
        if rate_limited:
            shot = output_dir / "chatgpt_rate_limit.png"
            assert self.page is not None
            self.page.screenshot(path=str(shot))
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=shot,
                rate_limited=True,
                error_message="ChatGPT rate limit detected on description project.",
                prompt_sent=desc_prompt,
                step="description_generate",
            )
        _step("description_copy_save", "Copying description")
        self.wait_for_response_complete()
        description_text = self.copy_latest_description() or self.latest_assistant_text() or body[-2000:]
        if not description_text.strip():
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=None,
                rate_limited=False,
                error_message="Description text was empty after generation.",
                prompt_sent=desc_prompt,
                step="description_copy_save",
            )
        description_path = self.save_description_file(output_dir, description_text)
        _step("description_saved", f"Saved description: {description_path}")
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
        on_step=None,
        open_channel: bool = False,
    ) -> ChatGPTGenerationResult:
        """Generate image only. Caller should open the image channel once for a batch."""
        def _step(name: str, message: str) -> None:
            if on_step:
                on_step(name, message)

        from core.prompts import safe_format

        if not self.ensure_ready():
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=None,
                rate_limited=False,
                error_message="ChatGPT login required for your account. Complete login in the dedicated Chrome window.",
                source_image_path=source_product_image,
                step="login",
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        src = Path(source_product_image)
        if not src.exists():
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=None,
                rate_limited=False,
                error_message="First scraped product image is required for model image generation.",
                source_image_path=src,
                step="image_generate",
            )
        vars_map = dict(prompt_vars or {})
        img_prompt = safe_format(image_prompt, **vars_map)
        if open_channel:
            self.open_project(self.settings.chatgpt_image_project_url)
        _step("image_generate", f"Generating model image ({src.name})")
        _body2, rate_limited2 = self.send_prompt(
            img_prompt,
            image_path=src,
            wait_seconds=self.settings.chatgpt_wait_seconds,
        )
        if rate_limited2:
            shot = output_dir / "chatgpt_rate_limit.png"
            assert self.page is not None
            self.page.screenshot(path=str(shot))
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=shot,
                rate_limited=True,
                error_message="ChatGPT rate limit detected during image generation project.",
                prompt_sent=img_prompt,
                source_image_path=src,
                step="image_generate",
            )
        self.wait_for_response_complete()
        assert self.page is not None
        try:
            self.page.wait_for_selector(
                "img[src*='oaiusercontent'], img[src*='oaidalle'], [data-message-author-role='assistant'] img",
                timeout=45000,
            )
        except Exception:  # noqa: BLE001
            self.page.wait_for_timeout(3000)
        image_path = self.capture_generated_image(output_dir / "0.png")
        if image_path is None:
            shot = output_dir / "chatgpt_image_capture_failed.png"
            return ChatGPTGenerationResult(
                success=False,
                description_text=None,
                image_path=None,
                screenshot_path=shot if shot.exists() else None,
                rate_limited=False,
                error_message="Model image download failed (UI screenshot is not used for 0.png).",
                prompt_sent=img_prompt,
                source_image_path=src,
                step="image_saved",
            )
        _step("image_saved", f"Saved model image: {image_path}")
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

    def generate_for_brand(
        self,
        *,
        brand_name: str,
        site_name: str,
        description_prompt: str,
        image_prompt: str,
        output_dir: Path,
        source_product_image: Path | None = None,
        on_step=None,
        prompt_vars: dict | None = None,
    ) -> ChatGPTGenerationResult:
        """Per-item path (opens both channels). Prefer batch two-pass in Engine2 worker."""
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
        desc = self.generate_description_only(
            description_prompt=description_prompt,
            output_dir=output_dir,
            prompt_vars=vars_map,
            on_step=on_step,
            open_channel=True,
        )
        if not desc.success:
            return desc
        if source_product_image is None or not Path(source_product_image).exists():
            return ChatGPTGenerationResult(
                success=False,
                description_text=desc.description_text,
                image_path=None,
                screenshot_path=None,
                rate_limited=False,
                error_message="First scraped product image is required for model image generation.",
                prompt_sent=image_prompt,
                source_image_path=source_product_image,
                description_path=desc.description_path,
                step="image_generate",
            )
        img = self.generate_image_only(
            image_prompt=image_prompt,
            output_dir=output_dir,
            source_product_image=Path(source_product_image),
            prompt_vars=vars_map,
            on_step=on_step,
            open_channel=True,
        )
        if not img.success:
            return ChatGPTGenerationResult(
                success=False,
                description_text=desc.description_text,
                image_path=None,
                screenshot_path=img.screenshot_path,
                rate_limited=img.rate_limited,
                error_message=img.error_message,
                prompt_sent=img.prompt_sent,
                source_image_path=source_product_image,
                description_path=desc.description_path,
                step=img.step,
            )
        return ChatGPTGenerationResult(
            success=True,
            description_text=desc.description_text,
            image_path=img.image_path,
            screenshot_path=None,
            rate_limited=False,
            prompt_sent=img.prompt_sent,
            source_image_path=source_product_image,
            description_path=desc.description_path,
            step="done",
        )


def download_first_product_image(image_url: str | None, target_path: Path) -> Path | None:
    if not image_url:
        return None
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(image_url)
            response.raise_for_status()
            target_path.write_bytes(response.content)
            return target_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to download product image %s: %s", image_url, exc)
        return None


def run_manual_chatgpt_login_test() -> dict[str, str]:
    """Deprecated helper — prefer scripts/chatgpt_cookie_login.py."""
    settings = get_settings()
    session = ChatGPTBrowserSession()
    session.start()
    assert session.page is not None
    print("Log in manually in Chrome if needed, then press ENTER to save cookies.")
    input()
    ready = session._is_logged_in()
    saved = session.save_session_cookies() if session.context else {}
    desc_url = settings.chatgpt_description_project_url
    image_url = settings.chatgpt_image_project_url
    session.close()
    return {
        "description_project": desc_url,
        "image_project": image_url,
        "login_status": "success" if ready else "needs_check",
        "cookies_saved": str(saved.get("path", "")),
        "cookie_count": str(saved.get("cookie_count", 0)),
    }
