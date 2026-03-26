from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.handlers import coding_stage_execution


def _make_deps(
    *,
    has_existing_run_contract: bool,
    stdout: str = "ok",
    stderr: str = "",
    files_written: list[str] | None = None,
) -> coding_stage_execution.StageExecutionDeps:
    send_action_with_heartbeat = AsyncMock(
        return_value={
            "status": "success",
            "result": {
                "returncode": 0,
                "stdout": stdout,
                "stderr": stderr,
                "files_written": list(files_written or [".pytest_cache/v/cache/nodeids"]),
            },
        }
    )
    return coding_stage_execution.StageExecutionDeps(
        cfg=SimpleNamespace(OPENCLAW_QUEUE_MODE="require_empty_queue", CONTROL_LOOP_DEFAULT_WORKER_ID="worker-primary"),
        logger=MagicMock(),
        send_action=AsyncMock(),
        send_action_with_heartbeat=send_action_with_heartbeat,
        get_openclaw_runner=lambda: None,
        stop_request_key=lambda user_id: f"stop:{user_id}",
        stage_payload=lambda **kwargs: dict(kwargs),
        action_inner_result=lambda result: dict(result.get("result") or {}),
        action_exit_code=lambda result: int((result.get("result") or {}).get("returncode", 0) or 0),
        action_error_text=lambda result, _action: str(result.get("error") or ""),
        action_excerpt=lambda result: str((result.get("result") or {}).get("stderr") or ""),
        normalize_written_files=lambda raw_files: [str(item) for item in list(raw_files or [])],
        has_runnable_written_files=lambda paths: any(str(path).endswith(".py") for path in paths),
        working_dir_has_valid_run_contract=AsyncMock(return_value=has_existing_run_contract),
        emit_runtime_trace_async=AsyncMock(),
        build_debug_bundle=lambda **kwargs: dict(kwargs),
        command_hash=lambda prompt: f"hash:{len(prompt)}",
        runtime_flow=lambda: "telegram_real",
        runtime_transport_label=lambda **_kwargs: "websocket_primary",
        runtime_mode_label=lambda **_kwargs: "worker_agent",
        use_acp_orchestration=lambda: False,
        acp_stage_name=lambda stage_name: stage_name,
        acp_backend_name=lambda stage_name: stage_name,
        record_orchestration_event=AsyncMock(),
        write_generated_blocks_to_worker=AsyncMock(return_value=([], "")),
    )


@pytest.mark.asyncio
async def test_stage_succeeds_when_existing_run_contract_is_valid() -> None:
    result = await coding_stage_execution.run_stage_chain_for_generation(
        deps=_make_deps(has_existing_run_contract=True),
        db=None,
        app=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock())),
        chat_id=1,
        user_id=1,
        project={"id": "proj-1"},
        task_id=143,
        prompt="verify existing runnable project",
        working_dir="E:/SKYNET-SANDBOX/Projects/demo",
        stage_chain=["qwen"],
        label_prefix="coding",
        require_runnable_files=True,
        notify_stage_switch=False,
    )

    assert result["ok"] is True
    assert result["stage_name"] == "qwen"
    assert result["stage_failures"] == []


@pytest.mark.asyncio
async def test_stage_fails_when_no_runnable_files_and_no_existing_run_contract() -> None:
    result = await coding_stage_execution.run_stage_chain_for_generation(
        deps=_make_deps(has_existing_run_contract=False),
        db=None,
        app=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock())),
        chat_id=1,
        user_id=1,
        project={"id": "proj-1"},
        task_id=143,
        prompt="verify existing runnable project",
        working_dir="E:/SKYNET-SANDBOX/Projects/demo",
        stage_chain=["qwen"],
        label_prefix="coding",
        require_runnable_files=True,
        notify_stage_switch=False,
    )

    assert result["ok"] is False
    assert result["stage_failures"] == [
        {
            "stage": "qwen",
            "returncode": "0",
            "error_excerpt": "no runnable files generated",
        }
    ]


@pytest.mark.asyncio
async def test_stage_fails_with_quota_excerpt_before_success_when_existing_run_contract_exists() -> None:
    result = await coding_stage_execution.run_stage_chain_for_generation(
        deps=_make_deps(
            has_existing_run_contract=True,
            stdout="Qwen OAuth quota exceeded: Your free daily quota has been reached.",
            stderr="",
            files_written=[".pytest_cache/v/cache/nodeids"],
        ),
        db=None,
        app=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock())),
        chat_id=1,
        user_id=1,
        project={"id": "proj-1"},
        task_id=143,
        prompt="verify existing runnable project",
        working_dir="E:/SKYNET-SANDBOX/Projects/demo",
        stage_chain=["qwen"],
        label_prefix="coding",
        require_runnable_files=True,
        notify_stage_switch=False,
    )

    assert result["ok"] is False
    assert result["failure_type"] == "ENVIRONMENT_FAILED"
    assert result["error_code"] == "PROVIDER_QUOTA_EXHAUSTED"
    assert result["stage_failures"] == [
        {
            "stage": "qwen",
            "returncode": "0",
            "error_excerpt": (
                "PROVIDER_QUOTA_EXHAUSTED: "
                "Qwen OAuth quota exceeded: Your free daily quota has been reached."
            ),
            "failure_type": "ENVIRONMENT_FAILED",
            "error_code": "PROVIDER_QUOTA_EXHAUSTED",
        }
    ]
