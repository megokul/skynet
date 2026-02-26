from __future__ import annotations

from pathlib import Path
import sys


def _ensure_paths() -> None:
    repo_root = Path(__file__).parent.parent
    gateway_root = str(repo_root / "openclaw-gateway")
    if gateway_root not in sys.path:
        sys.path.insert(0, gateway_root)


_ensure_paths()

from core import tracing


def test_trace_logger_writes_required_structure(tmp_path: Path, monkeypatch) -> None:
    trace_file = tmp_path / "logs" / "skynet.trace.log"
    monkeypatch.setattr(tracing, "_TRACE_FILE", trace_file)

    logger = tracing.TraceLogger(
        trace_id="conv_xxxxxxxxx",
        user_id="123456",
        entrypoint="handle_text()",
        input_text="user message",
    )
    token = tracing.set_current_trace(logger)
    try:
        @tracing.trace(
            role="igris",
            prompt="prompts/supervisor/intent_classifier.md",
            step_name="classify_intent",
        )
        def classify_intent(text: str) -> dict[str, str]:
            return {"intent": "project.create"}

        classify_intent("user message")
    finally:
        logger.end()
        tracing.clear_current_trace(token)

    content = trace_file.read_text(encoding="utf-8")
    assert "TRACE START" in content
    assert "trace_id: conv_xxxxxxxxx" in content
    assert "entrypoint: handle_text()" in content
    assert 'input: "user message"' in content
    assert "[STEP 1] classify_intent()" in content
    assert "file: test_trace_logger.py" in content
    assert "path: tests/test_trace_logger.py" in content
    assert "role: igris" in content
    assert "prompt: prompts/supervisor/intent_classifier.md" in content
    assert "parameters:" in content
    assert '  text: "user message"' in content
    assert "result:" in content
    assert '  intent: "project.create"' in content
    assert "execution_time:" in content
    assert "TRACE END" in content
    assert "total_execution_time:" in content
