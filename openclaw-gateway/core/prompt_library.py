"""
Prompt file loader/composer for core role and orchestration prompts.

All prompt text is sourced from `openclaw-gateway/prompts` to keep policy and
behavior text auditable outside runtime code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

_PROMPT_ROOT = (
    Path(__file__).resolve().parents[1]
    / "prompts"
)


@lru_cache(maxsize=256)
def load_prompt(relative_path: str) -> str:
    """
    Load one prompt file by repo-relative path under `openclaw-gateway/prompts`.

    Missing files return empty string instead of raising so callers can degrade
    gracefully in non-critical paths.
    """
    path = _PROMPT_ROOT / relative_path
    if not path.exists():
        return ""
    raw = path.read_text(encoding="utf-8")
    return _strip_header_comment(raw).strip()


@lru_cache(maxsize=64)
def compose_prompt(files: tuple[str, ...]) -> str:
    """Join multiple prompt fragments with blank-line separators."""
    parts = [load_prompt(name) for name in files]
    return "\n\n".join(part for part in parts if part)


def render_prompt(relative_path: str, **kwargs: object) -> str:
    """
    Load and format a prompt template with named variables.

    Prompt files use Python `str.format` placeholders.
    """
    template = load_prompt(relative_path)
    if not template:
        return ""
    return template.format(**kwargs)


def commander_prompt_block() -> str:
    """Return baseline style/policy guidance for commander-facing prompts."""
    return compose_prompt(
        (
            "active/concise_output_short.md",
            "active/avoid_over_engineering.md",
            "active/no_unnecessary_additions.md",
            "active/executing_actions_with_care.md",
        )
    )


def engineering_prompt_block() -> str:
    """Return baseline style/policy guidance for engineering-focused responses."""
    return compose_prompt(
        (
            "active/software_engineering_focus.md",
            "active/read_before_modifying.md",
            "active/security.md",
            "active/avoid_over_engineering.md",
        )
    )


def _strip_header_comment(text: str) -> str:
    """Drop leading HTML comment blocks used as prompt metadata headers."""
    cleaned = re.sub(r"^<!--.*?-->\s*", "", text, flags=re.DOTALL)
    return cleaned
