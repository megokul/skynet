"""Prompt-reference integrity test suite.

Purpose:
- Scan Python source for referenced prompt paths.
- Ensure every declared prompt reference resolves to a real file.
- Prevent runtime failures from stale or renamed prompt assets."""

from __future__ import annotations

from pathlib import Path
import re


PROMPT_REF_RE = re.compile(r'prompt\s*=\s*"(?P<ref>prompts/[^"]+)"')


def test_all_prompt_references_exist_under_prompt_repo() -> None:
    """
    Test scenario `test_all_prompt_references_exist_under_prompt_repo`.
    
    Purpose:
    - Implement `test_all_prompt_references_exist_under_prompt_repo` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    repo_root = Path(__file__).resolve().parents[1]
    gateway_root = repo_root / "openclaw-gateway"
    missing: list[str] = []

    for py_file in gateway_root.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for match in PROMPT_REF_RE.finditer(text):
            prompt_ref = match.group("ref")
            rel = prompt_ref.removeprefix("prompts/")
            prompt_path = gateway_root / "prompts" / rel
            if not prompt_path.exists():
                missing.append(
                    f"{py_file.relative_to(repo_root)} -> {prompt_ref} (expected: {prompt_path.relative_to(repo_root)})"
                )

    assert not missing, "Missing prompt files for code references:\n" + "\n".join(sorted(missing))

