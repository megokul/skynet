"""Structured YAML trace regression tests for `core/dev_trace.py`."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest


def _ensure_paths() -> None:
    repo_root = Path(__file__).parent.parent
    gateway_root = str(repo_root / "openclaw-gateway")
    if gateway_root not in sys.path:
        sys.path.insert(0, gateway_root)


_ensure_paths()

from core import dev_trace
from core.dev_trace import DevTracePhase


@pytest.fixture
def trace_capture(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    captured: list[str] = []

    def _capture(text: str) -> None:
        captured.append(text)

    monkeypatch.setattr(dev_trace, "_append_trace_block", _capture)
    return captured


def test_dev_trace_renders_yaml_ledger_structure(trace_capture: list[str]) -> None:
    session, token = dev_trace.start_trace_session(
        trace_id="conv_trace_1",
        user_id="123456",
        user_input="create project called orbit",
    )
    try:
        session.record_control_flow(
            DevTracePhase.ENTRY,
            file="openclaw-gateway/bot/commands.py",
            line=2029,
            function="handle_text",
            params={"text": "create project called orbit"},
        )
        session.record_data_flow(
            DevTracePhase.ENTRY,
            source_name="user_input",
            source_value="  create project called orbit  ",
            target_name="normalized_input",
            target_value="create project called orbit",
        )
        session.record_prompt(
            DevTracePhase.INTENT,
            prompt_file="core/intent_extract_user.md",
            model="gpt-oss-120b",
        )
        session.record_decision(
            DevTracePhase.INTENT,
            {
                "classifier_confidence": 0.98,
                "selected_intent": "project.create",
            },
        )
        session.record_role_enter(DevTracePhase.ROUTING, "igris")
        session.record_role_switch(
            DevTracePhase.ROUTING,
            from_role="igris",
            to_role="project_specialist",
        )
        session.record_state_mutation(
            DevTracePhase.ROUTING,
            key="conversation.active_role",
            old_value="igris",
            new_value="project_specialist",
        )
        session.record_output(
            DevTracePhase.RESPONSE,
            key="assistant_response",
            value="Project created. What should it do?",
        )
    finally:
        session.end()
        dev_trace.clear_trace_session(token)

    assert len(trace_capture) == 1
    content = trace_capture[0]
    assert "trace_id: conv_trace_1" in content
    assert "timestamp:" in content
    assert "user_id: 123456" in content
    assert "input:" in content
    assert '  text: "create project called orbit"' in content
    assert "call_sequence:" in content
    assert "depth: 0" in content
    assert 'phase: "PHASE 1 - Entry & Normalisation"' in content
    assert 'file: "openclaw-gateway/bot/commands.py"' in content
    assert "params:" in content
    assert "prompt:" in content
    assert 'file: "core/intent_extract_user.md"' in content
    assert 'model: "gpt-oss-120b"' in content
    assert "data_flow:" in content
    assert 'from: "user_input"' in content
    assert 'to: "normalized_input"' in content
    assert "decision_reasoning:" in content
    assert "role_events:" in content
    assert "state_change:" in content
    assert "conversation.active_role:" in content
    assert 'before: "igris"' in content
    assert 'after: "project_specialist"' in content
    assert "output:" in content
    assert '"assistant_response": "Project created. What should it do?"' in content
    assert "data_lineage:" in content
    assert 'normalized_input: "create project called orbit"' in content
    assert "summary:" in content
    assert "total_time_ms:" in content
    assert "decisions:" in content
    assert "role_chain:" in content
    assert "END TRACE" in content
    assert "[STEP" not in content


def test_dev_trace_append_only_and_mutation_filter(trace_capture: list[str]) -> None:
    first, first_token = dev_trace.start_trace_session(
        trace_id="conv_trace_a",
        user_id="u1",
        user_input="A",
    )
    try:
        first.record_state_mutation(
            DevTracePhase.ENTRY,
            key="unchanged_key",
            old_value="same",
            new_value="same",
        )
    finally:
        first.end()
        dev_trace.clear_trace_session(first_token)

    second, second_token = dev_trace.start_trace_session(
        trace_id="conv_trace_b",
        user_id="u2",
        user_input="B",
    )
    try:
        second.record_output(DevTracePhase.RESPONSE, key="result", value="ok")
    finally:
        second.end()
        dev_trace.clear_trace_session(second_token)

    assert len(trace_capture) == 2
    combined = "".join(trace_capture)
    assert combined.count("END TRACE") == 2
    assert "trace_id: conv_trace_a" in combined
    assert "trace_id: conv_trace_b" in combined
    assert combined.index("trace_id: conv_trace_a") < combined.index("trace_id: conv_trace_b")
    assert "unchanged_key" not in combined
