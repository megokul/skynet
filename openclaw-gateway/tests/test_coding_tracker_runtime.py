from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pytest

from bot.handlers import coding_tracker_runtime


def _deps(
    *,
    state: dict[str, object],
    enabled: bool = True,
) -> coding_tracker_runtime.TrackerLifecycleDeps:
    state_api = types.SimpleNamespace(
        build_tracker_initial_state=lambda **kwargs: dict(state, **kwargs),
        apply_tracker_updates=lambda current, **kwargs: current.update(
            {key: value for key, value in kwargs.items() if value is not None}
        ),
    )
    logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
    return coding_tracker_runtime.TrackerLifecycleDeps(
        cfg=types.SimpleNamespace(OPENCLAW_QUEUE_MODE="require_empty_queue"),
        logger=logger,
        state_api=state_api,
        use_acp_orchestration=lambda: False,
        tracker_enabled=lambda: enabled,
        tracker_default_transport=lambda: "ssh_first",
        tracker_default_runtime_mode=lambda: "ssh",
        tracker_render_text=lambda current: f"{current.get('phase')}|{current.get('stage')}|{current.get('percent', 0)}",
        tracker_reply_markup=lambda current: f"markup:{current.get('status')}",
        tracker_state_key=lambda user_id, project_id: f"tracker:{user_id}:{project_id}",
        active_project_key=lambda user_id: f"active:{user_id}",
        tracker_get_state=lambda *, bot_data, user_id, project_id: bot_data.get(f"tracker:{user_id}:{project_id}"),
        tracker_edit_interval=lambda: 0,
    )


@pytest.mark.asyncio
async def test_update_tracker_message_replaces_missing_message() -> None:
    state = {
        "project_id": "proj-1",
        "message_id": 10,
        "phase": "setup",
        "status": "running",
        "percent": 10,
        "last_rendered_text": "",
        "last_edit_monotonic": 0.0,
        "created_monotonic": 0.0,
        "last_signal_monotonic": 0.0,
    }
    app = types.SimpleNamespace(
        bot=types.SimpleNamespace(
            edit_message_text=AsyncMock(side_effect=RuntimeError("message to edit not found")),
            send_message=AsyncMock(return_value=types.SimpleNamespace(message_id=55)),
        ),
        bot_data={"tracker:99:proj-1": state, "active:99": "proj-1"},
    )

    await coding_tracker_runtime.update_tracker_message(
        deps=_deps(state=state),
        app=app,
        chat_id=7,
        user_id=99,
        project_id="proj-1",
        request=coding_tracker_runtime.TrackerUpdateRequest(stage="codex"),
    )

    assert state["message_id"] == 55
    assert state["stage"] == "codex"


def test_tracker_render_text_delegates_to_state_api() -> None:
    seen: dict[str, object] = {}

    class _StateApi:
        @staticmethod
        def tracker_render_text(state, **kwargs):
            seen["state"] = state
            seen["kwargs"] = kwargs
            return "rendered"

    rendered = coding_tracker_runtime.tracker_render_text(
        state={"phase": "setup"},
        tracker_state_api=_StateApi(),
        bar_width=20,
        stale_warn_seconds_value=90,
        verbose_pipeline=True,
    )

    assert rendered == "rendered"
    assert seen["kwargs"] == {
        "bar_width": 20,
        "stale_warn_seconds_value": 90,
        "verbose_pipeline": True,
    }
