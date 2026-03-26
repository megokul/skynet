from __future__ import annotations

from security.validator import validate_params
from skynet.project_specialist import ready_sentence
from skynet.prompt_library import load_prompt, render_prompt


def test_qwen_context_text_is_exempt_from_shell_meta_validation() -> None:
    validate_params(
        {
            "qwen_context_text": render_prompt(
                "testing/common/validator_qwen_context.md",
                ready_sentence=ready_sentence(),
            ),
            "prompt": load_prompt("testing/common/next_reply_only.txt"),
        }
    )


def test_requirement_summary_md_is_exempt_from_shell_meta_validation() -> None:
    validate_params(
        {
            "requirement_summary_md": (
                "- Project Kind: local terminal utility script\n"
                "- Constraints: show a popup saying \"hi\" and play a beep\n"
                "- Integrations: none"
            ),
            "reply_contract": "emit_ready_sentence",
        }
    )
