from __future__ import annotations

from security.validator import validate_params


def test_qwen_context_text_is_exempt_from_shell_meta_validation() -> None:
    validate_params(
        {
            "qwen_context_text": "Ignore the workspace.\nUse this exact reply: 'I have everything I need.'",
            "prompt": "Write the next reply only.",
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
