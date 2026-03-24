from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import e2e_live
from live_pytest_flow import (
    PytestLiveFlowSpec,
    run_pytest_live_flow,
    summarize_telegram_tracker_output,
)


class _Trace:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.events: list[tuple[str, dict]] = []

    def log(self, event: str, **fields) -> None:
        self.events.append((event, dict(fields)))


def test_summarize_telegram_tracker_output_logs_summary(tmp_path: Path) -> None:
    trace = _Trace(tmp_path / "trace.log")

    summarize_telegram_tracker_output(
        '{"event":"tracker.message.detected"}\n{"event":"tracker.message.edited"}\n',
        trace.log,
    )

    assert any(event == "telegram_real.tracker.summary" for event, _ in trace.events)


def test_run_pytest_live_flow_fails_on_strict_skip(tmp_path: Path) -> None:
    trace = _Trace(tmp_path / "trace.log")
    failures: list[tuple[str, str]] = []

    def _runner(**_kwargs):
        return subprocess.CompletedProcess(
            args=["pytest"],
            returncode=0,
            stdout="1 skipped\n",
            stderr="",
        )

    def _fail(_trace, message: str, *, detail=None):
        failures.append((message, str(detail or "")))
        raise RuntimeError(message)

    spec = PytestLiveFlowSpec(
        flow="conversation",
        target="tests/test_fake.py::test_example",
        step="conversation_flow",
        invoke_event="conversation.invoke",
        exit_event="conversation.exit",
        subprocess_label="conversation_pytest",
        success_banner="[OK] passed",
        failure_message="Conversation live E2E failed.",
        strict_skip_message="Conversation live E2E was skipped under strict mode.",
        infer_infra_category=True,
    )

    with pytest.raises(RuntimeError, match="skipped under strict mode"):
        run_pytest_live_flow(
            trace=trace,
            cleanup=object(),
            spec=spec,
            repo_root="E:/MyProjects/skynet",
            build_env=lambda env: env,
            apply_policy_env=lambda env, _flow: env,
            run_subprocess_with_cleanup=_runner,
            fail_fn=_fail,
            strict_skip=True,
            infer_infra_category_fn=lambda _output: "timeout",
        )

    assert failures[0][0] == "Conversation live E2E was skipped under strict mode."
    assert any(event == "conversation.exit" for event, _ in trace.events)


def test_worker_status_ready_accepts_legacy_status_contract() -> None:
    ready, missing = e2e_live._worker_status_ready(
        policy={
            "required_transport": "websocket_primary",
            "required_worker_agents": ["qwen"],
        },
        status_payload={
            "primary_transport_mode": "websocket_primary",
            "agent_connected": True,
            "websocket_health_ok": True,
            "coding_agents": {"qwen": "/usr/bin/qwen"},
        },
    )

    assert ready is True
    assert missing == []
