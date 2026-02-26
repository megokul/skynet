from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def _ensure_paths() -> None:
    repo_root = Path(__file__).parent.parent
    gateway_root = str(repo_root / "openclaw-gateway")
    if gateway_root not in sys.path:
        sys.path.insert(0, gateway_root)


class _FakeProviderResponse:
    def __init__(self, text: str = "", tool_calls: list | None = None):
        self.text = text
        self.tool_calls = tool_calls or []


class _FakeProviderRouter:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []

    async def chat(self, messages, *, tools=None, system=None, max_tokens=0, task_type="general", allowed_providers=None):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "system": system,
                "task_type": task_type,
                "allowed_providers": allowed_providers,
            }
        )
        return _FakeProviderResponse(text=json.dumps(self.payload), tool_calls=[])

    def available_provider_names(self, **kwargs):
        return ["ollama"]


class _FakeSkill:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, tool_name, tool_input, context):
        self.calls.append((tool_name, dict(tool_input)))
        if tool_name == "project_add_idea":
            return f"Idea added to {tool_input.get('project_id')}: {tool_input.get('idea')}"
        if tool_name == "project_create":
            return "Project created."
        if tool_name == "project_approve_start":
            return "Plan approved."
        return f"Executed {tool_name}"


class _FakeSkillRegistry:
    def __init__(self, skill: _FakeSkill):
        self.skill = skill

    def get_skill_for_tool(self, tool_name: str):
        if tool_name in {"project_add_idea", "project_create", "project_approve_start"}:
            return self.skill
        return None

    def get_all_tools(self):
        return []


class _FakeSessionLoader:
    def __init__(self, session):
        self.session = session
        self.updates: list[dict] = []

    async def load(self, telegram_user_id: int):
        return self.session

    async def update(self, session, **changes):
        self.updates.append(changes)
        if "session_metadata" in changes:
            merged = dict(session.metadata)
            merged.update(changes["session_metadata"] or {})
            session.metadata = merged
        if "project_id" in changes:
            session.project_id = changes["project_id"]
        if "conversation_phase" in changes:
            session.conversation_phase = changes["conversation_phase"]
        if "last_mode" in changes:
            session.last_mode = changes["last_mode"]
        if "last_intent" in changes:
            session.last_intent = changes["last_intent"]

    async def clear_pending_action(self, session):
        session.metadata["pending_action"] = None


class _FakeUser:
    id = 999


class _FakeChat:
    id = 999


class _FakeUpdate:
    effective_user = _FakeUser()
    effective_chat = _FakeChat()
    message = SimpleNamespace(message_id=1)


def _make_session(*, project_id: str | None, metadata: dict | None = None):
    from bot.session import Session

    return Session(
        user_id="999",
        project_id=project_id,
        conversation_phase="planning" if project_id else "discovery",
        last_intent="",
        last_mode="planning",
        last_message_at=None,
        time_gap=None,
        metadata=metadata or {},
        project={"id": project_id, "name": "new_proj_test", "display_name": "new_proj_test"} if project_id else None,
    )


async def _build_orchestrator(*, session, extractor_payload: dict):
    _ensure_paths()
    from bot import state
    from bot.orchestrator import Orchestrator

    provider = _FakeProviderRouter(extractor_payload)
    skill = _FakeSkill()
    registry = _FakeSkillRegistry(skill)
    orchestrator = Orchestrator(
        db=None,
        project_manager=SimpleNamespace(db=None),
        provider_router=provider,
        skill_registry=registry,
        gateway_api_url="http://127.0.0.1:8766",
        chat_provider_allowlist=["ollama"],
    )
    orchestrator.session_loader = _FakeSessionLoader(session)

    async def _noop(*args, **kwargs):
        return None

    async def _no_history(*args, **kwargs):
        return []

    async def _no_last_assistant(*args, **kwargs):
        return ""

    orchestrator._persist_message = _noop
    orchestrator._load_recent_for_classifier = _no_history
    orchestrator._load_last_assistant_text = _no_last_assistant

    class _Persona:
        @staticmethod
        def compose_final_response(text: str) -> str:
            return text

        @staticmethod
        def compose_system_prompt(text: str) -> str:
            return text

    state._main_persona_agent = _Persona()
    state._chat_history = []

    return orchestrator, skill, provider


@pytest.mark.asyncio
async def test_active_project_write_intent_executes_without_scope_question() -> None:
    session = _make_session(project_id="proj-active")
    orch, skill, _ = await _build_orchestrator(
        session=session,
        extractor_payload={"idea_text": "python app with tkinter and display yurekaaa and beep", "confidence": 0.93},
    )

    reply = await orch._handle_internal(_FakeUpdate(), "python app with tkinter and display yurekaaa include beep")
    assert "existing project" not in reply.lower()
    assert "new project" not in reply.lower()
    assert skill.calls, "project_add_idea should be executed deterministically"
    tool_name, tool_input = skill.calls[0]
    assert tool_name == "project_add_idea"
    assert tool_input["project_id"] == "proj-active"


@pytest.mark.asyncio
async def test_new_prefix_without_pending_question_stays_on_active_project() -> None:
    session = _make_session(project_id="proj-active")
    orch, skill, _ = await _build_orchestrator(
        session=session,
        extractor_payload={"idea_text": "new idea: add tkinter popup with beep", "confidence": 0.88},
    )

    reply = await orch._handle_internal(_FakeUpdate(), "new idea: add tkinter popup with beep")
    assert "which project" not in reply.lower()
    assert skill.calls, "write intent should execute on active project"
    _, tool_input = skill.calls[0]
    assert tool_input["project_id"] == "proj-active"


@pytest.mark.asyncio
async def test_pending_scope_question_interprets_new_as_scope_answer() -> None:
    from datetime import datetime, timedelta, timezone

    session = _make_session(
        project_id="proj-active",
        metadata={
            "pending_question": {
                "type": "choose_project_scope",
                "choices": ["existing", "new"],
                "turn_id": "t123",
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                "payload": {},
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    orch, skill, _ = await _build_orchestrator(
        session=session,
        extractor_payload={"idea_text": "", "confidence": 0.0},
    )

    reply = await orch._handle_internal(_FakeUpdate(), "new")
    assert "name" in reply.lower()
    assert not skill.calls, "no write tool should run until project name is provided"
    assert orch.session_loader.updates, "session metadata should be updated with new pending question"
    last_update = orch.session_loader.updates[-1]
    pending = (last_update.get("session_metadata") or {}).get("pending_question") or {}
    assert pending.get("type") == "need_project_name"
