"""Cognitive trace regression tests for `core/dev_trace.py`."""

from __future__ import annotations

from pathlib import Path
import sys


def _ensure_paths() -> None:
    repo_root = Path(__file__).parent.parent
    gateway_root = str(repo_root / "openclaw-gateway")
    if gateway_root not in sys.path:
        sys.path.insert(0, gateway_root)


_ensure_paths()

from core import dev_trace
from core.dev_trace import DevTracePhase


def test_dev_trace_writes_required_cognitive_structure(tmp_path: Path, monkeypatch) -> None:
    trace_file = tmp_path / "logs" / "skynet.trace.log"
    monkeypatch.setattr(dev_trace, "_TRACE_FILE", trace_file)

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
        )
        session.record_data_flow(
            DevTracePhase.ENTRY,
            source_name="user_input",
            source_value="  create project called orbit  ",
            target_name="normalized_input",
            target_value="create project called orbit",
        )
        session.record_decision(
            DevTracePhase.INTENT,
            {
                "classifier_confidence": 0.98,
                "evaluated_intents": ["project.create (0.98)", "weather.query (0.01)"],
                "selected_intent": "project.create",
                "reasoning": "highest confidence",
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

    content = trace_file.read_text(encoding="utf-8")
    assert "TRACE conv_trace_1" in content
    assert "USER INPUT:" in content
    assert '  "create project called orbit"' in content
    assert "PHASE 1 — Entry & Normalisation" in content
    assert "PHASE 2 — Intent Resolution" in content
    assert "PHASE 3 — Role Routing" in content
    assert "PHASE 4 — Specialist Execution" in content
    assert "PHASE 5 — Role Restoration" in content
    assert "PHASE 6 — Response Construction" in content
    assert "CONTROL FLOW" in content
    assert "└── openclaw-gateway/bot/commands.py:2029::handle_text(...)" in content
    assert "DATA FLOW" in content
    assert "→ normalized_input" in content
    assert "DECISION REASONING" in content
    assert "[ROLE ENTER] igris" in content
    assert "[ROLE SWITCH]" in content
    assert "STATE MUTATION" in content
    assert "conversation.active_role:" in content
    assert '"igris" → "project_specialist"' in content
    assert "OUTPUT" in content
    assert 'assistant_response = "Project created. What should it do?"' in content
    assert "TRACE SUMMARY" in content
    assert "Decision Points:" in content
    assert "Role Transitions:" in content
    assert "igris → project_specialist" in content
    assert "END TRACE" in content
    assert "[STEP" not in content


def test_dev_trace_is_append_only_and_filters_unchanged_mutations(tmp_path: Path, monkeypatch) -> None:
    trace_file = tmp_path / "logs" / "skynet.trace.log"
    monkeypatch.setattr(dev_trace, "_TRACE_FILE", trace_file)

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

    content = trace_file.read_text(encoding="utf-8")
    assert content.count("END TRACE") == 2
    assert "TRACE conv_trace_a" in content
    assert "TRACE conv_trace_b" in content
    assert "unchanged_key" not in content
