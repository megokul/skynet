from __future__ import annotations

from security.validator import validate_params


def test_qwen_context_text_is_exempt_from_shell_meta_validation() -> None:
    validate_params(
        {
            "qwen_context_text": "Ignore the workspace.\nUse this exact reply: 'I have everything I need.'",
            "prompt": "Write the next reply only.",
        }
    )
