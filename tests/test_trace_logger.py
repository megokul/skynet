"""Strict tree trace regression tests for `core/dev_trace.py`."""

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


def test_dev_trace_renders_strict_tree_format(trace_capture: list[str]) -> None:
    session, token = dev_trace.start_trace_session(
        trace_id="conv_8d8680b870c14458",
        user_id="7152683074",
        user_input="i want to start a project",
    )
    try:
        session.set_header_field("conversation_id", "conv_8d8680b870c14458")
        session.set_header_field("telegram_user_id", "7152683074")
        session.set_header_field("active_role", "igris")
        session.set_header_field("message_count_before", 42)
        session.set_header_field("message_count_after", 44)
        session.set_header_field("turn", 44)
        session.set_header_field("final_active_role", "igris")

        source_id = session.record_control_flow(
            DevTracePhase.ENTRY,
            file="openclaw-gateway/bot/commands.py",
            line=2009,
            function="handle_text",
            params={"text": "i want to start a project"},
        )
        session.mark_source_node(source_id)
        session.record_context(
            DevTracePhase.ENTRY,
            node_id=source_id,
            values={
                "conversation_id": "conv_8d8680b870c14458",
                "incoming_message_id": 43,
            },
        )

        run_id = session.record_control_flow(
            DevTracePhase.ROUTING,
            file="openclaw-gateway/core/engine.py",
            line=443,
            function="run",
            params={"conversation_id": "conv_8d8680b870c14458", "active_role": "igris"},
        )
        session.mark_run_node(run_id)

        classify_id = session.record_control_flow(
            DevTracePhase.INTENT,
            file="openclaw-gateway/core/intent_extractor.py",
            line=131,
            function="classify_intent",
            parent_id=run_id,
            params={"user_input": "i want to start a project"},
        )
        session.record_prompt(
            DevTracePhase.INTENT,
            node_id=classify_id,
            prompt_file="core/intent_extract_user.md",
            model="router:auto",
        )
        session.record_output(DevTracePhase.INTENT, node_id=classify_id, key="intent", value="project.create")
        session.record_output(DevTracePhase.INTENT, node_id=classify_id, key="confidence", value=0.98)

        select_id = session.record_control_flow(
            DevTracePhase.ROUTING,
            file="openclaw-gateway/core/roles/igris.py",
            line=184,
            function="select_specialist",
            parent_id=run_id,
            params={"intent": "project.create"},
        )
        session.record_output(
            DevTracePhase.ROUTING,
            node_id=select_id,
            key="selected_role",
            value="project_specialist",
        )

        append_id = session.record_control_flow(
            DevTracePhase.SPECIALIST,
            file="openclaw-gateway/core/roles/project_specialist.py",
            line=346,
            function="_append_idea_to_active",
            parent_id=run_id,
            params={
                "project_id": "ffbb755decb0",
                "idea_text": "i want to start a project",
            },
        )
        session.record_state_mutation(
            DevTracePhase.SPECIALIST,
            node_id=append_id,
            key="idea_count",
            old_value=0,
            new_value=1,
        )
        session.record_output(DevTracePhase.SPECIALIST, node_id=append_id, key="success", value=True)

        restore_id = session.record_control_flow(
            DevTracePhase.RESTORATION,
            file="openclaw-gateway/core/conversation_manager.py",
            line=167,
            function="set_active_role",
            parent_id=run_id,
            params={"new_role": "igris"},
        )
        session.record_state_mutation(
            DevTracePhase.RESTORATION,
            node_id=restore_id,
            key="active_role",
            old_value="project_specialist",
            new_value="igris",
        )

        session.record_role_enter(DevTracePhase.ROUTING, role="igris", node_id=run_id)
        session.record_role_switch(
            DevTracePhase.ROUTING,
            from_role="igris",
            to_role="project_specialist",
            node_id=run_id,
        )
        session.record_role_switch(
            DevTracePhase.RESTORATION,
            from_role="project_specialist",
            to_role="igris",
            node_id=restore_id,
        )

        session.record_output(
            DevTracePhase.ROUTING,
            node_id=run_id,
            key="response",
            value="Idea added to testthisshit. Igris is back in command.",
        )

        assistant_id = session.record_control_flow(
            DevTracePhase.RESPONSE,
            file="openclaw-gateway/core/conversation_manager.py",
            line=240,
            function="add_message",
            params={
                "role": "assistant",
                "content": "Idea added to testthisshit. Igris is back in command.",
            },
        )
        session.mark_assistant_message_node(assistant_id)
        session.record_state_mutation(
            DevTracePhase.RESPONSE,
            node_id=assistant_id,
            key="message_count",
            old_value=43,
            new_value=44,
        )
        session.record_output(
            DevTracePhase.RESPONSE,
            node_id=assistant_id,
            key="outgoing_message_id",
            value=44,
        )
    finally:
        session.end()
        dev_trace.clear_trace_session(token)

    assert len(trace_capture) == 1
    content = trace_capture[0]

    # ── header box ──
    assert "TRACE conv_8d8680b870c14458 | turn=44" in content
    assert "TIME  " in content
    assert "7152683074" in content
    assert "igris" in content
    assert "42" in content and "44" in content
    assert '"i want to start a project"' in content

    # ── execution tree ──
    assert "▼ EXECUTION" in content
    assert "openclaw-gateway/bot/commands.py:2009 → handle_text(" in content
    assert 'conversation_id="conv_8d8680b870c14458"' in content
    assert "incoming_message_id=43" in content
    assert "openclaw-gateway/core/engine.py:443 → run(" in content
    assert 'response="Idea added to testthisshit. Igris is back in command."' in content

    # ── state changes ──
    assert "▼ STATE CHANGES" in content
    assert "igris → project_specialist → igris" in content
    assert "⟳ role:" in content

    # ── footer ──
    assert "END TRACE" in content
    assert "━" in content


def test_dev_trace_append_only_and_omits_missing_lines(trace_capture: list[str]) -> None:
    first, first_token = dev_trace.start_trace_session(
        trace_id="conv_trace_a",
        user_id="u1",
        user_input="A",
    )
    try:
        first.set_header_field("conversation_id", "conv_trace_a")
    finally:
        first.end()
        dev_trace.clear_trace_session(first_token)

    second, second_token = dev_trace.start_trace_session(
        trace_id="conv_trace_b",
        user_id="u2",
        user_input="B",
    )
    try:
        second.set_header_field("conversation_id", "conv_trace_b")
        second.set_header_field("message_count_before", 3)
        second.set_header_field("message_count_after", 4)
        second.set_header_field("turn", 4)
    finally:
        second.end()
        dev_trace.clear_trace_session(second_token)

    assert len(trace_capture) == 2
    combined = "".join(trace_capture)
    assert combined.count("END TRACE") == 2
    assert "TRACE conv_trace_a" in combined
    assert "TRACE conv_trace_b | turn=4" in combined
    assert combined.index("TRACE conv_trace_a") < combined.index("TRACE conv_trace_b")
    # Missing lines should be omitted when values are unavailable.
    first_block = trace_capture[0]
    assert "message_count_before:" not in first_block
    assert "message_count:" not in first_block
