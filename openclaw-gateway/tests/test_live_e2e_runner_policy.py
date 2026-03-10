from __future__ import annotations

import pytest

import e2e_live
import test_e2e_telegram_real_live as telegram_real_live


class _Trace:
    path = "trace.log"

    def log(self, *_args, **_kwargs) -> None:
        return None


def test_infer_infra_category_capacity() -> None:
    text = "SSH action failed: Exceeded MaxStartups while opening session"
    assert e2e_live._infer_infra_category(text) == "capacity"


def test_check_env_fails_when_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_SSH_HOST", raising=False)
    monkeypatch.delenv("OPENCLAW_SSH_USER", raising=False)
    monkeypatch.setenv("SKYNET_E2E_TRANSPORT", "ssh")
    monkeypatch.setenv("SKYNET_E2E_FAIL_ON_SKIP", "1")

    def _fail(trace, message, *, detail=None):
        raise RuntimeError(f"{message} | {detail}")

    monkeypatch.setattr(e2e_live, "_fail", _fail)
    with pytest.raises(RuntimeError, match="environment validation failed"):
        e2e_live._check_env(_Trace())


def test_check_env_can_skip_when_not_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_SSH_HOST", raising=False)
    monkeypatch.delenv("OPENCLAW_SSH_USER", raising=False)
    monkeypatch.setenv("SKYNET_E2E_TRANSPORT", "ssh")
    monkeypatch.setenv("SKYNET_E2E_FAIL_ON_SKIP", "0")

    with pytest.raises(SystemExit) as exc:
        e2e_live._check_env(_Trace())
    assert exc.value.code == 0


def test_terminal_coding_failure_text_detects_tracker_final_failure() -> None:
    text = (
        "Coding Progress [##------------------] 10%\n"
        "Phase: Finalization - Worker unavailable before coding started\n"
        "Status: Failed"
    )
    assert telegram_real_live._terminal_coding_failure_text(text) == text


def test_terminal_coding_failure_text_detects_direct_failure_message() -> None:
    text = "Worker not connected - cannot create project folder."
    assert telegram_real_live._terminal_coding_failure_text(text) == text


def test_strict_stage_policy_violation_detects_fallback_message() -> None:
    text = "⚠️ Stage qwen failed (no runnable files generated). Trying codex..."
    assert (
        telegram_real_live._strict_stage_policy_violation_text(
            text,
            allowed_stages={"qwen"},
        )
        == text
    )


def test_strict_stage_policy_violation_detects_tracker_stage_switch() -> None:
    text = (
        "Coding Progress [####----------------] 20%\n"
        "Phase: Milestone Execution - Running stage codex (2/2)\n"
        "Pipeline: stage=codex | runtime=ssh"
    )
    assert (
        telegram_real_live._strict_stage_policy_violation_text(
            text,
            allowed_stages={"qwen"},
        )
        == text
    )
