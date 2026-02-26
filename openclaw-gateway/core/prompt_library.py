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
    path = _PROMPT_ROOT / relative_path
    if not path.exists():
        return ""
    raw = path.read_text(encoding="utf-8")
    return _strip_header_comment(raw).strip()


@lru_cache(maxsize=64)
def compose_prompt(files: tuple[str, ...]) -> str:
    parts = [load_prompt(name) for name in files]
    return "\n\n".join(part for part in parts if part)


def render_prompt(relative_path: str, **kwargs: object) -> str:
    template = load_prompt(relative_path)
    if not template:
        return ""
    return template.format(**kwargs)


def commander_prompt_block() -> str:
    return compose_prompt(
        (
            "active/concise_output_short.md",
            "active/avoid_over_engineering.md",
            "active/no_unnecessary_additions.md",
            "active/executing_actions_with_care.md",
        )
    )


def engineering_prompt_block() -> str:
    return compose_prompt(
        (
            "active/software_engineering_focus.md",
            "active/read_before_modifying.md",
            "active/security.md",
            "active/avoid_over_engineering.md",
        )
    )


def _strip_header_comment(text: str) -> str:
    cleaned = re.sub(r"^<!--.*?-->\s*", "", text, flags=re.DOTALL)
    return cleaned
