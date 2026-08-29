from __future__ import annotations

from pathlib import Path

from core.config import get_settings
from core.prompts.defaults import BUYMA_DESCRIPTION_PROMPT, BUYMA_IMAGE_PROMPT


def safe_format(template: str, **kwargs: str) -> str:
    """Format template while leaving unknown `{...}` placeholders intact."""
    class _Safe(dict):
        def __missing__(self, key: str) -> str:  # type: ignore[override]
            return "{" + key + "}"

    try:
        return template.format_map(_Safe(**kwargs))
    except Exception:  # noqa: BLE001
        return template


def _prompt_override_path(kind: str) -> Path:
    settings = get_settings()
    return settings.secrets_dir / "prompts" / f"buyma_{kind}_prompt.txt"


def default_description_prompt() -> str:
    override = _prompt_override_path("description")
    if override.exists():
        try:
            text = override.read_text(encoding="utf-8").strip()
            if text:
                return text
        except Exception:  # noqa: BLE001
            pass
    return BUYMA_DESCRIPTION_PROMPT


def default_image_prompt() -> str:
    override = _prompt_override_path("image")
    if override.exists():
        try:
            text = override.read_text(encoding="utf-8").strip()
            if text:
                return text
        except Exception:  # noqa: BLE001
            pass
    return BUYMA_IMAGE_PROMPT
