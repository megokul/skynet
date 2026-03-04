from __future__ import annotations

import pytest

import e2e_live


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
    monkeypatch.setenv("SKYNET_E2E_FAIL_ON_SKIP", "1")

    def _fail(trace, message, *, detail=None):
        raise RuntimeError(f"{message} | {detail}")

    monkeypatch.setattr(e2e_live, "_fail", _fail)
    with pytest.raises(RuntimeError, match="environment validation failed"):
        e2e_live._check_env(_Trace())


def test_check_env_can_skip_when_not_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_SSH_HOST", raising=False)
    monkeypatch.delenv("OPENCLAW_SSH_USER", raising=False)
    monkeypatch.setenv("SKYNET_E2E_FAIL_ON_SKIP", "0")

    with pytest.raises(SystemExit) as exc:
        e2e_live._check_env(_Trace())
    assert exc.value.code == 0

