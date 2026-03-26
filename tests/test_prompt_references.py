from __future__ import annotations

from pathlib import Path
import re

from skynet.prompt_library import load_prompt, resolve_prompt_path


PROMPT_CALL_RE = re.compile(r'(?:load_prompt|render_prompt)\(\s*["\'](?P<ref>[^"\']+)["\']')

PROMPT_RUNTIME_FILES = [
    "openclaw-gateway/bot/handlers/chat.py",
    "openclaw-gateway/bot/handlers/coding.py",
    "openclaw-gateway/bot/handlers/coding_planning.py",
    "openclaw-gateway/orchestration/critic.py",
    "openclaw-gateway/orchestration/director.py",
    "openclaw-gateway/orchestration/architect.py",
    "openclaw-gateway/ssh_tunnel_executor.py",
    "openclaw-gateway/ai/context.py",
    "openclaw-gateway/ai/prompts.py",
    "skynet/project_specialist.py",
    "skynet/qwen_cli.py",
]

BANNED_RUNTIME_PROMPT_REFS = [
    "gateway/chat/system.md",
    "gateway/coding/system.md",
    "gateway/planning/project_specialist_system.md",
    "gateway/orchestration/director_contract.md",
    "gateway/orchestration/architect_contract.md",
    "gateway/orchestration/critic_review.md",
    "gateway/planning/ready_sentence.txt",
]


def _runtime_audit_snippets() -> list[str]:
    snippets: list[str] = []
    for prompt_ref in BANNED_RUNTIME_PROMPT_REFS:
        first_line = next(
            (line.strip() for line in load_prompt(prompt_ref).splitlines() if line.strip()),
            "",
        )
        if first_line:
            snippets.append(first_line)
    return snippets


def test_all_prompt_library_references_resolve() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    missing: list[str] = []

    for rel_path in PROMPT_RUNTIME_FILES:
        py_file = repo_root / rel_path
        text = py_file.read_text(encoding="utf-8")
        for match in PROMPT_CALL_RE.finditer(text):
            prompt_ref = match.group("ref")
            try:
                prompt_path = resolve_prompt_path(prompt_ref)
            except Exception as exc:
                missing.append(f"{rel_path} -> {prompt_ref} ({type(exc).__name__}: {exc})")
                continue
            if not prompt_path.exists():
                missing.append(f"{rel_path} -> {prompt_ref} (missing: {prompt_path})")

    assert not missing, "Missing prompt files for code references:\n" + "\n".join(sorted(missing))


def test_runtime_modules_do_not_embed_primary_prompt_literals() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    audit_snippets = _runtime_audit_snippets()

    for rel_path in PROMPT_RUNTIME_FILES:
        text = (repo_root / rel_path).read_text(encoding="utf-8")
        for snippet in audit_snippets:
            if snippet in text:
                offenders.append(f"{rel_path} -> {snippet}")

    assert not offenders, "Runtime modules still contain hardcoded prompt text:\n" + "\n".join(sorted(offenders))
