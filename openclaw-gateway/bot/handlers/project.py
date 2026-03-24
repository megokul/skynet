"""
SKYNET Bot â€” Project Creation Flow

ConversationHandler states:
    AWAITING_PROJECT_NAME  (1) â€” waiting for the user to type a project name
    AWAITING_PROJECT_TYPE  (2) â€” waiting for the user to tap a project type button
    GATHERING_REQUIREMENTS (3) â€” Project Specialist AI gathers requirements
    REVIEWING_PLAN         (4) â€” user reviewing the generated plan

Entry point: user taps "ðŸš€ Start a Project" from the main menu.
Exit:        project saved to DB with approved plan; confirmation sent.
Cancel:      /cancel or /start at any point.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import uuid
from typing import Any

import gateway_config as cfg
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.keyboards import (
    CB_PLAN_APPROVE,
    CB_PLAN_CHANGES,
    CB_REQUIREMENTS_DONE,
    CB_START_PROJECT,
    PROJECT_TYPE_LABELS,
    main_menu,
    plan_review,
    project_type,
    requirements_done,
    start_coding,
)
from bot.handlers.project_session import (
    NAME_KEY as _NAME_KEY,
    PLAN_KEY as _PLAN_KEY,
    PLANNER_STATE_KEY as _PLANNER_STATE_KEY,
    REQS_HISTORY_KEY as _REQS_HISTORY,
    TYPE_KEY as _TYPE_KEY,
    ProjectConversationSession,
)
from bot.project_templates import get_template
from bot.state import KEY_DB, KEY_ROUTER
from db.store import create_project, ensure_user
from gateway import is_worker_available, send_action
from orchestration.openclaw_runner import get_openclaw_runner
from runtime_trace import build_debug_bundle, command_hash, emit_runtime_trace_async
from skynet.project_specialist import (
    build_planner_state,
    build_planner_chat_prompt,
    build_requirement_summary_markdown,
    build_qwen_plan_generation_context,
    build_qwen_planner_context,
    build_qwen_planner_prompt,
    build_project_specialist_opening,
    build_project_specialist_system_prompt,
    is_qwen_plan_generation_request,
    ready_sentence,
)

logger = logging.getLogger("skynet.bot.project")

# â”€â”€ Conversation states â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
AWAITING_PROJECT_NAME  = 1
AWAITING_PROJECT_TYPE  = 2
GATHERING_REQUIREMENTS = 3
REVIEWING_PLAN         = 4

# Max turns kept in requirements conversation history
_MAX_REQS_TURNS = 30
_PLAN_REQUIRED_MARKERS = ("overview", "core features", "tech stack", "project structure", "milestones")
_PLAN_BANNED_PHRASES = (
    "planner assistant mode is active",
    "role set:",
    "i am now operating as",
    "i can help you plan",
    "i'll act as your planner assistant",
    "iâ€™ll act as your planner assistant",
    "you must generate the full project plan now",
    "telegram product workflow",
    "send the first item",
    "send your current item",
)


def _runtime_flow() -> str:
    raw = str(cfg.get_str("SKYNET_LIVE_E2E_FLOW", "") or "").strip().lower()
    if raw in {"telegram_real", "conversation", "direct"}:
        return raw
    return "direct"


def _live_e2e_runtime_policy() -> dict[str, object]:
    flow = _runtime_flow()
    if flow not in {"telegram_real", "conversation"}:
        return {}
    if not bool(cfg.get_bool("SKYNET_E2E_LIVE", getattr(cfg, "E2E_LIVE", False))):
        return {}
    policy = cfg.get_live_e2e_policy(flow)
    return dict(policy) if isinstance(policy, dict) else {}


async def _emit_runtime(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    update: Update | None,
    event: str,
    status: str = "ok",
    phase: str = "project",
    project_id: str = "",
    error_code: str = "",
    error_message: str = "",
    failure_class: str = "",
    details: dict | None = None,
    action_name: str = "",
) -> None:
    db = context.bot_data.get(KEY_DB) if isinstance(context.bot_data, dict) else None
    tg_user = update.effective_user if update is not None else None
    chat = update.effective_chat if update is not None else None
    debug_bundle = None
    if status.strip().lower() in {"fail", "failed", "error"}:
        debug_bundle = build_debug_bundle(
            failure_class=failure_class or error_code or "UNKNOWN",
            error_message=error_message,
            causal_chain=[event],
            mitigation_hint="Inspect planner trace and fallback path.",
        )
    await emit_runtime_trace_async(
        db=db,
        event=event,
        status=status,
        flow=_runtime_flow(),
        phase=phase,
        project_id=project_id,
        telegram_chat_id=str(getattr(chat, "id", "") or ""),
        telegram_user_id=str(getattr(tg_user, "id", "") or ""),
        action_name=action_name,
        command_hash=command_hash(action_name),
        error_code=error_code,
        error_message=error_message,
        failure_class=failure_class or error_code,
        details=details or {},
        debug_bundle=debug_bundle,
    )


# â”€â”€ System prompt â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _specialist_prompt(name: str, project_type_label: str, template: dict) -> str:
    return build_project_specialist_system_prompt(name, project_type_label, template)


def _planner_state_for_history(name: str, project_type_label: str, history: list[dict]) -> dict[str, Any]:
    return build_planner_state(
        project_name=str(name or "").strip(),
        project_type_label=str(project_type_label or "").strip(),
        messages=list(history or []),
    )


def _planner_reply_contract(messages: list[dict], planner_state: dict[str, Any]) -> str:
    if is_qwen_plan_generation_request(list(messages or [])):
        return "emit_plan"
    if bool(dict(planner_state or {}).get("plan_ready", False)):
        return "emit_ready_sentence"
    return "ask_next_question"


def _build_project_description(plan: str, history: list[dict]) -> str:
    """Persist approved plan together with original user requirement snippets."""
    approved_plan = (plan or "").strip()
    seen: set[str] = set()
    requirements: list[str] = []

    for item in history:
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        compact = " ".join(text.split())
        if not compact:
            continue
        lowered = compact.lower()
        if lowered.startswith("generate the full project plan now based on everything"):
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        requirements.append(compact)

    if not requirements:
        return approved_plan

    req_block = "\n".join(f"- {line}" for line in requirements[-20:])
    pieces = [approved_plan, f"Original user requirements:\n{req_block}"]
    return "\n\n".join(part for part in pieces if part).strip()


def _requirement_terms(history: list[dict]) -> set[str]:
    stopwords = {
        "with", "from", "that", "this", "your", "have", "what", "when", "where",
        "which", "would", "could", "should", "into", "about", "there", "their",
        "project", "build", "building", "please", "need", "want", "using",
    }
    terms: set[str] = set()
    for item in history:
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        text = str(item.get("content") or "").strip().lower()
        if not text or text.startswith("generate the full project plan now"):
            continue
        for token in re.findall(r"[a-z0-9][a-z0-9_+.-]{2,}", text):
            if token.isdigit() or token in stopwords:
                continue
            terms.add(token)
    return terms


def _requirement_lines(history: list[dict], *, limit: int = 8) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for item in history:
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered.startswith("generate the full project plan now"):
            continue
        compact = " ".join(text.split())
        if not compact:
            continue
        key = compact.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(compact)
    return lines[-limit:]


def _is_requirement_grounded_plan(plan: str, history: list[dict]) -> bool:
    plan_text = (plan or "").strip()
    if len(plan_text) < 120:
        return False
    lowered = plan_text.lower()
    if any(marker not in lowered for marker in _PLAN_REQUIRED_MARKERS):
        return False
    if _looks_like_meta_planner_output(plan_text):
        return False
    terms = _requirement_terms(history)
    if not terms:
        return True
    overlap = sum(1 for term in terms if term in lowered)
    return overlap >= min(2, len(terms))


def _looks_like_meta_planner_output(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return True
    return any(phrase in lowered for phrase in _PLAN_BANNED_PHRASES)


def _build_deterministic_plan(
    *,
    name: str,
    project_type_label: str,
    template: dict,
    history: list[dict],
) -> str:
    req_lines = _requirement_lines(history)
    req_joined = " ".join(req_lines).lower()
    is_windows = "windows" in req_joined
    mentions_python = "python" in req_joined or "python" in str(template.get("stack", "")).lower()
    mentions_popup = "popup" in req_joined or "messagebox" in req_joined
    mentions_beep = "beep" in req_joined or "sound" in req_joined

    overview_bits = []
    if req_lines:
        overview_bits.append(req_lines[-1])
    if is_windows and mentions_python:
        overview_bits.append("Target platform is Windows and execution is from terminal.")
    overview = " ".join(overview_bits).strip() or (
        f"{name} is a {project_type_label} project with a clear, implementation-ready scope."
    )

    features: list[str] = []
    if mentions_popup:
        features.append('Display a popup window showing "hi" when the script runs')
    if mentions_beep:
        features.append("Play a short beep sound during execution")
    if mentions_python:
        features.append("Use Python standard library only where possible")
    for line in req_lines[-3:]:
        if len(features) >= 4:
            break
        if line.lower() not in ("python", "windows", "terminal execution"):
            features.append(line)
    if not features:
        features = [
            "Implement the requested core behavior",
            "Add basic validation and error handling",
            "Include a runnable entrypoint and tests",
        ]

    structure = [
        f"{name.lower().replace(' ', '_')}.py",
        "README.md",
        "tests/test_main.py",
        "skynet_run.json",
    ]

    milestones = [
        "Implement the main script with the required runtime behavior",
        "Add tests and validate terminal execution path",
        "Add skynet_run.json and confirm the project runs successfully",
    ]

    stack = str(template.get("stack") or "").strip() or "Python 3.10+"
    if is_windows and "windows" not in stack.lower():
        stack = f"{stack}; Windows runtime"

    features_block = "\n".join(f"  - {item}" for item in features[:5])
    structure_block = "\n".join(f"  - {item}" for item in structure)
    milestones_block = "\n".join(
        f"  {idx}. {item}" for idx, item in enumerate(milestones, start=1)
    )

    return (
        f"**{name} â€” Project Plan**\n"
        f"**Overview:** {overview}\n\n"
        f"**Core Features:**\n"
        f"{features_block}\n\n"
        f"**Tech Stack:**\n"
        f"  - {stack}\n\n"
        f"**Project Structure:**\n"
        f"{structure_block}\n\n"
        f"**Milestones:**\n"
        f"{milestones_block}\n\n"
        f"**Open Questions:** None"
    )


def _planner_sandbox_dir(user_id: int, session_key: str) -> str:
    return f"{cfg.WORKER_PROJECTS_DIR}/_planner_sessions/{user_id}/{session_key}"


def _planner_action_text(result: dict) -> str:
    inner = result.get("result", result)
    text = str(inner.get("assistant_text") or inner.get("stdout") or "").strip()
    return text


def _planner_task_mode(
    agent: str,
    *,
    messages: list[dict] | None = None,
    reply_contract: str = "",
) -> str:
    if str(agent or "").strip().lower() != "qwen":
        return ""
    if str(reply_contract or "").strip().lower() == "emit_plan":
        return "plan_generation"
    if is_qwen_plan_generation_request(list(messages or [])):
        return "plan_generation"
    return "planner_chat"


def _planner_qwen_context(
    task_mode: str,
    system: str,
    planner_state: dict[str, Any],
    *,
    reply_contract: str = "",
) -> str:
    if str(task_mode or "").strip().lower() == "plan_generation":
        return build_qwen_plan_generation_context(system, planner_state)
    return build_qwen_planner_context(system, planner_state, reply_contract=reply_contract)


def _planner_prompt_and_payload(
    agent: str,
    messages: list[dict],
    system: str,
    planner_state: dict[str, Any],
    reply_contract: str,
) -> tuple[str, str, str, str]:
    planner_agent = str(agent or "").strip().lower()
    if planner_agent != "qwen":
        return build_planner_chat_prompt(system, messages), "", "", ""
    task_mode = _planner_task_mode(planner_agent, messages=messages, reply_contract=reply_contract)
    requirement_summary_md = build_requirement_summary_markdown(planner_state)
    return (
        build_qwen_planner_prompt(
            messages,
            planner_state=planner_state,
            reply_contract=reply_contract,
        ),
        task_mode,
        _planner_qwen_context(task_mode, system, planner_state, reply_contract=reply_contract),
        requirement_summary_md,
    )


def _planner_qwen_uses_request_scoped_dir(task_mode: str) -> bool:
    policy = cfg.get_qwen_execution_policy()
    profile = dict(policy.get("planner") or {})
    if str(task_mode or "").strip().lower() == "plan_generation":
        profile = dict(policy.get("planner") or {})
    return str(profile.get("working_dir_strategy") or "project").strip().lower() == "request_scoped"


def _planner_primary_agent() -> str:
    live_policy = _live_e2e_runtime_policy()
    live_agents = list(live_policy.get("required_planner_agents") or [])
    if live_agents:
        agent = str(live_agents[0] or "router").strip().lower()
    else:
        agent = str(getattr(cfg, "PLANNER_PRIMARY_AGENT", "router") or "router").strip().lower()
    if agent == "claude_ollama":
        return "claude"
    return agent


def _planner_router_fallback_enabled() -> bool:
    live_policy = _live_e2e_runtime_policy()
    if live_policy:
        return bool(live_policy.get("planner_router_fallback_enabled", False))
    return bool(getattr(cfg, "PLANNER_ROUTER_FALLBACK_ENABLED", True))


def _planner_worker_agents() -> set[str]:
    return {
        str(item).strip().lower()
        for item in getattr(cfg, "PLANNER_WORKER_AGENTS", ())
        if str(item).strip()
    }


def _planner_acp_agents() -> set[str]:
    return {
        str(item).strip().lower()
        for item in getattr(cfg, "PLANNER_ACP_AGENTS", ())
        if str(item).strip()
    }


async def _planner_via_codex_then_router(
    *,
    router,
    messages: list[dict],
    system: str,
    max_tokens: int,
    task_type: str,
    user_id: int,
    planner_state: dict[str, Any] | None = None,
) -> str:
    planner_agent = _planner_primary_agent()
    use_planner_agent = planner_agent in _planner_worker_agents()
    allow_router_fallback = _planner_router_fallback_enabled()
    if not use_planner_agent and not allow_router_fallback:
        raise RuntimeError(
            f"Planner fallback is disabled and the primary agent '{planner_agent}' is not eligible."
        )
    if use_planner_agent:
        current_planner_state = dict(planner_state or {})
        if not current_planner_state:
            current_planner_state = _planner_state_for_history("planner-session", "Python App", messages)
        reply_contract = _planner_reply_contract(messages, current_planner_state)
        planner_prompt, planner_task_mode, qwen_context_text, requirement_summary_md = _planner_prompt_and_payload(
            planner_agent,
            messages,
            system,
            current_planner_state,
            reply_contract,
        )
        timeout = max(
            30,
            int(
                getattr(
                    cfg,
                    "CONTROL_LOOP_PLANNER_TIMEOUT_SECONDS",
                    getattr(cfg, "PLANNER_CODEX_TIMEOUT_SECONDS", 120),
                )
                or 120
            ),
        )
        planner_session_key = uuid.uuid4().hex
        use_request_scoped_dir = planner_agent == "qwen" and _planner_qwen_uses_request_scoped_dir(planner_task_mode)
        sandbox_dir = "" if use_request_scoped_dir else _planner_sandbox_dir(user_id, planner_session_key)
        try:
            orchestration_mode = str(cfg.effective_orchestration_mode() or "legacy").strip().lower()
            if orchestration_mode == "acp_first" and planner_agent in _planner_acp_agents():
                runner = get_openclaw_runner()
                session = await runner.start_session(
                    phase="planner",
                    project_id=f"user-{user_id}",
                    task_id=None,
                    stage=planner_agent,
                    runtime=str(getattr(cfg, "OPENCLAW_RUNTIME", "acp") or "acp"),
                    queue_mode="soft",
                )
                run_result = await runner.run_prompt(
                    session_id=str(session.get("session_id") or ""),
                    prompt=planner_prompt,
                    timeout_seconds=timeout,
                    stage=planner_agent,
                    backend="native",
                )
                return_code = int(run_result.get("returncode", 1) or 1)
                if return_code != 0:
                    detail = str(
                        run_result.get("stderr")
                        or run_result.get("stdout")
                        or f"planner {planner_agent} failed"
                    )
                    raise RuntimeError(detail)
                text = str(run_result.get("stdout") or "").strip()
            else:
                if not is_worker_available():
                    raise RuntimeError(f"Worker unavailable for planner {planner_agent} call")
                if sandbox_dir:
                    await send_action(
                        "create_directory",
                        {
                            "directory": sandbox_dir,
                            "project_id": f"user-{user_id}",
                            "worker_id": str(getattr(cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "") or "worker-primary"),
                            "session_key": planner_session_key,
                        },
                        timeout=20,
                        confirmed=True,
                    )
                result = await send_action(
                    "run_coding_agent",
                    {
                        "agent": planner_agent,
                        "backend": "auto",
                        **({"task_mode": planner_task_mode} if planner_task_mode else {}),
                        **({"qwen_context_text": qwen_context_text} if qwen_context_text else {}),
                        **({"reply_contract": reply_contract} if reply_contract else {}),
                        **({"planner_state_json": current_planner_state} if current_planner_state else {}),
                        **({"requirement_summary_md": requirement_summary_md} if requirement_summary_md else {}),
                        "prompt": planner_prompt,
                        **({"working_dir": sandbox_dir} if sandbox_dir else {}),
                        "timeout_seconds": timeout,
                        "project_id": f"user-{user_id}",
                        "task_id": f"planner-{task_type}",
                        "worker_id": str(getattr(cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "") or "worker-primary"),
                        "session_key": planner_session_key,
                    },
                    timeout=timeout,
                    confirmed=True,
                )
                if result.get("status") == "error":
                    raise RuntimeError(str(result.get("error") or "run_coding_agent failed"))
                inner = result.get("result", result)
                return_code = int(inner.get("returncode", inner.get("exit_code", 0)) or 0)
                if return_code != 0:
                    detail = str(
                        inner.get("stderr")
                        or inner.get("stdout")
                        or f"planner {planner_agent} failed"
                    )
                    raise RuntimeError(detail)
                text = _planner_action_text(result)
            if not text:
                raise RuntimeError(f"planner {planner_agent} returned empty output")
            if _looks_like_meta_planner_output(text):
                raise RuntimeError(f"planner {planner_agent} returned meta assistant text")
            return text
        except Exception as exc:
            logger.warning(
                "planner.primary.failover user_id=%s stage=%s error=%s",
                user_id,
                planner_agent,
                str(exc)[:220],
            )
            if not allow_router_fallback:
                raise
        finally:
            if sandbox_dir and is_worker_available():
                with contextlib.suppress(Exception):
                    await send_action(
                        "delete_directory",
                        {
                            "directory": sandbox_dir,
                            "project_id": f"user-{user_id}",
                            "worker_id": str(getattr(cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "") or "worker-primary"),
                            "session_key": planner_session_key,
                        },
                        timeout=20,
                        confirmed=True,
                    )

    if not allow_router_fallback and use_planner_agent:
        raise RuntimeError(
            f"Planner fallback is disabled and the primary agent '{planner_agent}' did not return a response."
        )
    response = await router.chat(
        messages=messages,
        system=system,
        max_tokens=max_tokens,
        task_type=task_type,
    )
    return (response.text or "").strip() or "â€¦"


# â”€â”€ Handlers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def ask_project_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Entry: user tapped 'Start a Project'."""
    await update.callback_query.answer()
    await _emit_runtime(
        context=context,
        update=update,
        event="project.start",
        status="start",
        phase="project_intake",
    )
    await update.callback_query.message.reply_text(
        "What should we call this project?"
    )
    await _emit_runtime(
        context=context,
        update=update,
        event="project.start",
        status="ok",
        phase="project_intake",
        details={"next_state": "awaiting_project_name"},
    )
    return AWAITING_PROJECT_NAME


async def receive_project_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User typed the project name."""
    session = ProjectConversationSession(context.user_data, max_history_turns=_MAX_REQS_TURNS)
    name = (update.message.text or "").strip()
    if not name:
        await _emit_runtime(
            context=context,
            update=update,
            event="project.name",
            status="fail",
            phase="project_intake",
            error_code="INVALID_NAME",
            error_message="Empty project name.",
            failure_class="CONTRACT_FAILED",
        )
        await update.message.reply_text("Please give the project a name.")
        return AWAITING_PROJECT_NAME

    session.set_project_name(name)
    await _emit_runtime(
        context=context,
        update=update,
        event="project.name",
        status="ok",
        phase="project_intake",
        details={"name": name[:120]},
    )
    await update.message.reply_text(
        f"What type of project is <b>{name}</b>?",
        parse_mode="HTML",
        reply_markup=project_type(),
    )
    return AWAITING_PROJECT_TYPE


async def receive_project_type(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User tapped a project type â€” hand off to Project Specialist."""
    await update.callback_query.answer()
    session = ProjectConversationSession(context.user_data, max_history_turns=_MAX_REQS_TURNS)

    cb_data    = update.callback_query.data or ""
    type_label = PROJECT_TYPE_LABELS.get(cb_data, "Other")
    name       = session.project_name("Untitled")
    template   = get_template(type_label)

    session.set_project_type(type_label)
    await _emit_runtime(
        context=context,
        update=update,
        event="project.type",
        status="ok",
        phase="project_intake",
        details={"name": str(name)[:120], "type": type_label},
    )

    # Opening message from the Project Specialist (seeded into history)
    opening = build_project_specialist_opening(name, type_label, template)

    session.seed_opening(
        opening=opening,
        project_name=str(name),
        project_type_label=type_label,
    )

    await update.callback_query.message.reply_text(
        opening, parse_mode="HTML", reply_markup=requirements_done(),
    )
    return GATHERING_REQUIREMENTS


async def handle_requirements_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Forward each user message to the Project Specialist LLM."""
    session = ProjectConversationSession(context.user_data, max_history_turns=_MAX_REQS_TURNS)
    user_text = (update.message.text or "").strip()
    if not user_text:
        await _emit_runtime(
            context=context,
            update=update,
            event="project.requirements.message",
            status="skip",
            phase="requirements",
            details={"reason": "empty_message"},
        )
        return GATHERING_REQUIREMENTS

    router = context.bot_data.get(KEY_ROUTER)
    if router is None:
        await _emit_runtime(
            context=context,
            update=update,
            event="project.requirements.message",
            status="fail",
            phase="requirements",
            error_code="ROUTER_UNAVAILABLE",
            error_message="AI router unavailable while collecting requirements.",
            failure_class="ENVIRONMENT_FAILED",
        )
        await update.message.reply_text("AI router is not available right now.")
        return GATHERING_REQUIREMENTS

    await update.effective_chat.send_action(ChatAction.TYPING)

    name       = session.project_name("Untitled")
    type_label = session.project_type("Other")
    template   = get_template(type_label)
    history: list[dict] = session.history()

    history = session.append_history("user", user_text)
    system_prompt = build_project_specialist_system_prompt(name, type_label, template)
    planner_state = session.refresh_planner_state(project_name=str(name), project_type_label=type_label)

    try:
        reply = await _planner_via_codex_then_router(
            router=router,
            messages=history,
            system=system_prompt,
            max_tokens=1024,
            task_type="planning",
            user_id=update.effective_user.id,
            planner_state=planner_state,
        )
    except Exception:
        logger.exception("Requirements AI call failed")
        await _emit_runtime(
            context=context,
            update=update,
            event="project.requirements.message",
            status="fail",
            phase="requirements",
            action_name="run_coding_agent",
            error_code="PLANNER_CALL_FAILED",
            error_message="Requirements AI call failed.",
            failure_class="GENERATION_FAILED",
        )
        await update.message.reply_text(
            "AI is unavailable right now. Please try again."
        )
        return GATHERING_REQUIREMENTS

    history = session.append_history("assistant", reply)
    planner_state = session.refresh_planner_state(project_name=str(name), project_type_label=type_label)
    await _emit_runtime(
        context=context,
        update=update,
        event="project.requirements.message",
        status="ok",
        phase="requirements",
        action_name="run_coding_agent",
        details={
            "history_len": len(history),
            "reply_len": len(reply),
            "plan_ready": bool(planner_state.get("plan_ready", False)),
            "missing_slots": list(planner_state.get("missing_slots") or []),
        },
    )

    await update.message.reply_text(reply, reply_markup=requirements_done())
    return GATHERING_REQUIREMENTS


async def requirements_done_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User tapped 'Done â€” Generate Plan' button during requirements chat."""
    await update.callback_query.answer()
    await _emit_runtime(
        context=context,
        update=update,
        event="project.plan.generate",
        status="start",
        phase="planning",
    )
    # Delegate directly to plan generation (same logic as /plan).
    return await _do_generate_plan(update.callback_query.message, context)


async def cmd_generate_plan(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User sent /plan â€” generate the full project plan."""
    return await _do_generate_plan(update.message, context)


async def _do_generate_plan(
    message,  # telegram.Message (from command or callback_query.message)
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Shared logic: call the Project Specialist to generate the plan."""
    session = ProjectConversationSession(context.user_data, max_history_turns=_MAX_REQS_TURNS)
    router = context.bot_data.get(KEY_ROUTER)
    if router is None:
        await emit_runtime_trace_async(
            db=context.bot_data.get(KEY_DB) if isinstance(context.bot_data, dict) else None,
            event="project.plan.generate",
            status="fail",
            flow=_runtime_flow(),
            phase="planning",
            error_code="ROUTER_UNAVAILABLE",
            error_message="AI router unavailable during plan generation.",
            failure_class="ENVIRONMENT_FAILED",
        )
        await message.reply_text("AI router is not available right now.")
        return GATHERING_REQUIREMENTS

    await message.chat.send_action(ChatAction.TYPING)
    await message.reply_text("Generating your project plan...")

    name       = session.project_name("Untitled")
    type_label = session.project_type("Other")
    template   = get_template(type_label)
    history: list[dict] = session.history()
    system_prompt = build_project_specialist_system_prompt(name, type_label, template)

    history = session.append_history(
        "user",
        "Generate the full project plan now based on everything we discussed.",
    )
    planner_state = session.refresh_planner_state(project_name=str(name), project_type_label=type_label)

    try:
        plan = await _planner_via_codex_then_router(
            router=router,
            messages=history,
            system=system_prompt,
            max_tokens=2048,
            task_type="planning",
            user_id=message.chat.id,
            planner_state=planner_state,
        )
        plan = plan.strip() or "Could not generate plan."
        await emit_runtime_trace_async(
            db=context.bot_data.get(KEY_DB) if isinstance(context.bot_data, dict) else None,
            event="project.plan.generate",
            status="ok",
            flow=_runtime_flow(),
            phase="planning",
            action_name="run_coding_agent",
            command_hash=command_hash(system_prompt),
            details={"plan_len": len(plan), "project_name": str(name)[:120]},
        )
    except Exception:
        logger.exception("Plan generation AI call failed")
        await emit_runtime_trace_async(
            db=context.bot_data.get(KEY_DB) if isinstance(context.bot_data, dict) else None,
            event="project.plan.generate",
            status="fail",
            level="error",
            flow=_runtime_flow(),
            phase="planning",
            action_name="run_coding_agent",
            command_hash=command_hash(system_prompt),
            error_code="PLANNER_CALL_FAILED",
            error_message="Plan generation AI call failed.",
            failure_class="GENERATION_FAILED",
            debug_bundle=build_debug_bundle(
                failure_class="GENERATION_FAILED",
                error_message="Plan generation AI call failed.",
                causal_chain=["project.plan.generate"],
                mitigation_hint="Check planner backend availability and response format.",
            ),
        )
        await message.reply_text("Could not generate the plan. Please try /plan again.")
        return GATHERING_REQUIREMENTS

    # Validate plan looks like a real plan (not a meta-response from the AI).
    is_real_plan = _is_requirement_grounded_plan(plan, history)
    if not is_real_plan:
        # Retry once with a more explicit prompt.
        logger.warning("Plan looks invalid (no milestones/structure) â€” retrying")
        retry_msg = (
            "You MUST generate the full project plan now. "
            "Include: Overview, Core Features, Tech Stack, Project Structure, and "
            "a numbered Milestones list. Ground each section in the user's stated requirements. "
            "Do NOT ask more questions and do NOT return role/meta text."
        )
        history.append({"role": "user", "content": retry_msg})
        try:
            retry_plan = await _planner_via_codex_then_router(
                router=router,
                messages=history,
                system=system_prompt,
                max_tokens=2048,
                task_type="planning",
                user_id=message.chat.id,
            )
            retry_plan = retry_plan.strip()
            if _is_requirement_grounded_plan(retry_plan, history):
                plan = retry_plan
        except Exception:
            pass  # Keep the original plan as fallback
        if not _is_requirement_grounded_plan(plan, history):
            logger.warning(
                "Plan remained invalid after retry; using deterministic requirement-grounded fallback plan."
            )
            plan = _build_deterministic_plan(
                name=name,
                project_type_label=type_label,
                template=template,
                history=history,
            )

    session.set_plan(plan)
    session.append_history("assistant", plan)

    await message.reply_text(plan, reply_markup=plan_review())
    return REVIEWING_PLAN


async def approve_plan(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User approved the plan â€” save project to DB and offer to start coding."""
    await update.callback_query.answer()
    session = ProjectConversationSession(context.user_data, max_history_turns=_MAX_REQS_TURNS)

    db      = context.bot_data.get(KEY_DB)
    tg_user = update.effective_user
    name       = session.project_name("Untitled")
    type_label = session.project_type("Other")
    plan       = session.plan("")
    history: list[dict] = session.history()
    project_description = _build_project_description(plan, history)

    try:
        coding_default = str(cfg.CODING_DEFAULT_PROFILE or "legacy").strip().lower()
        if coding_default not in {"legacy", "claude_ollama", "codex_primary"}:
            coding_default = "legacy"

        default_profile = cfg.STRICT_QUALITY_GATES_DEFAULT_PROFILE
        if default_profile not in {"strict", "legacy"}:
            default_profile = "strict"
        quality_profile = (
            default_profile if cfg.STRICT_QUALITY_GATES_ENABLED else "legacy"
        )
        if bool(getattr(cfg, "CONTROL_LOOP_FORCE_FOR_ALL", False)):
            control_loop_profile = "loop_v2"
        elif bool(getattr(cfg, "CONTROL_LOOP_ENABLED", True)):
            control_loop_profile = str(
                getattr(cfg, "CONTROL_LOOP_DEFAULT_PROFILE", "loop_v2") or "loop_v2"
            ).strip().lower()
            if control_loop_profile not in {"legacy", "loop_v1", "loop_v2"}:
                control_loop_profile = "loop_v2"
        else:
            control_loop_profile = "legacy"
        user = await ensure_user(
            db,
            telegram_user_id=tg_user.id,
            username=tg_user.username    or "",
            first_name=tg_user.first_name or "",
            last_name=tg_user.last_name   or "",
        )
        project = await create_project(
            db,
            user_id=user["id"],
            name=name,
            project_type=type_label,
            description=project_description,
            coding_profile=coding_default,
            quality_profile=quality_profile,
            control_loop_profile=control_loop_profile,
        )
        await _emit_runtime(
            context=context,
            update=update,
            event="project.plan.approve",
            status="ok",
            phase="planning",
            project_id=str(project.get("id") or ""),
            details={"project_type": type_label, "project_name": str(name)[:120]},
        )
    except Exception:
        logger.exception("Failed to save project name=%r type=%r", name, type_label)
        await _emit_runtime(
            context=context,
            update=update,
            event="project.plan.approve",
            status="fail",
            phase="planning",
            error_code="PROJECT_SAVE_FAILED",
            error_message="Failed to save approved project.",
            failure_class="ENVIRONMENT_FAILED",
        )
        await update.callback_query.message.reply_text(
            "Something went wrong saving the project. Please try again.",
            reply_markup=main_menu(),
        )
        _clear_user_data(context)
        return ConversationHandler.END

    context.user_data["last_project_id"] = project["id"]
    _clear_user_data(context)

    await update.callback_query.message.reply_text(
        f"âœ… <b>{project['name']}</b> saved!\n"
        f"Type: {project['project_type']}\n\n"
        "Ready to build it?",
        parse_mode="HTML",
        reply_markup=start_coding(),
    )
    return ConversationHandler.END


async def request_changes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User wants changes â€” return to requirements chat."""
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Sure â€” what would you like to change or add?\n"
        "Send /plan again when you're ready for a new version."
    )
    return GATHERING_REQUIREMENTS




async def cancel_project(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Fallback: /cancel or /start exits the conversation."""
    _clear_user_data(context)
    await update.effective_message.reply_text(
        "Project creation cancelled.",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _clear_user_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    ProjectConversationSession(context.user_data, max_history_turns=_MAX_REQS_TURNS).clear()



# â”€â”€ Builder â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def build_project_conversation_handler() -> ConversationHandler:
    """
    Wire the full project + specialist flow into a single ConversationHandler.
    Registered at group 0 so it intercepts text before the greeting handler.
    """
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(ask_project_name, pattern=f"^{CB_START_PROJECT}$"),
        ],
        states={
            AWAITING_PROJECT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_project_name),
            ],
            AWAITING_PROJECT_TYPE: [
                CallbackQueryHandler(receive_project_type, pattern=r"^type:"),
            ],
            GATHERING_REQUIREMENTS: [
                CallbackQueryHandler(requirements_done_handler, pattern=f"^{CB_REQUIREMENTS_DONE}$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_requirements_message),
            ],
            REVIEWING_PLAN: [
                CallbackQueryHandler(approve_plan,    pattern=f"^{CB_PLAN_APPROVE}$"),
                CallbackQueryHandler(request_changes, pattern=f"^{CB_PLAN_CHANGES}$"),
            ],
        },
        fallbacks=[
            CommandHandler("plan",   cmd_generate_plan),
            CommandHandler("cancel", cancel_project),
            CommandHandler("start",  cancel_project),
        ],
        allow_reentry=True,
        per_message=False,
    )

