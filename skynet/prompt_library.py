from __future__ import annotations

import os
from pathlib import Path
from typing import Any


_PROMPT_ENV_VAR = "SKYNET_PROMPT_LIBRARY_DIR"
_DEFAULT_PROMPT_ROOT = Path(__file__).resolve().parents[1] / "prompts"


def prompt_root() -> Path:
    override = str(os.environ.get(_PROMPT_ENV_VAR, "") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_PROMPT_ROOT.resolve()


def normalize_prompt_ref(prompt_ref: str) -> str:
    ref = str(prompt_ref or "").strip().replace("\\", "/")
    if ref.startswith("prompts/"):
        ref = ref[len("prompts/") :]
    return ref.lstrip("/")


def resolve_prompt_path(prompt_ref: str) -> Path:
    ref = normalize_prompt_ref(prompt_ref)
    if not ref:
        raise ValueError("prompt_ref must not be empty")
    root = prompt_root()
    path = (root / ref).resolve()
    if root not in path.parents and path != root:
        raise ValueError(f"Prompt reference escapes prompt root: {prompt_ref!r}")
    return path


def load_prompt(prompt_ref: str) -> str:
    path = resolve_prompt_path(prompt_ref)
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


class _PromptFormatMap(dict[str, Any]):
    def __missing__(self, key: str) -> Any:
        raise KeyError(f"Missing prompt variable: {key}")


def render_prompt(prompt_ref: str, /, **values: Any) -> str:
    template = load_prompt(prompt_ref)
    rendered = template.format_map(_PromptFormatMap(values))
    return str(rendered).strip()


def list_prompt_refs(*, prefix: str = "") -> list[str]:
    root = prompt_root()
    if not root.exists():
        return []
    normalized_prefix = normalize_prompt_ref(prefix)
    if normalized_prefix:
        base = root / normalized_prefix
        if not base.exists():
            return []
        candidates = sorted(path for path in base.rglob("*") if path.is_file())
    else:
        candidates = sorted(path for path in root.rglob("*") if path.is_file())
    refs: list[str] = []
    for path in candidates:
        refs.append(path.relative_to(root).as_posix())
    return refs
