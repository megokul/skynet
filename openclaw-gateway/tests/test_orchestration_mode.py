from __future__ import annotations

import config as cfg


def test_effective_orchestration_mode_forces_legacy_in_ssh_mode(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "ssh_tunnel")
    monkeypatch.setattr(cfg, "ORCHESTRATION_MODE", "acp_first")
    monkeypatch.setattr(cfg, "ORCHESTRATION_ALLOW_ACP_WITH_SSH", False)
    assert cfg.effective_orchestration_mode() == "legacy"


def test_effective_orchestration_mode_allows_acp_with_override(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "ssh_tunnel")
    monkeypatch.setattr(cfg, "ORCHESTRATION_MODE", "acp_first")
    monkeypatch.setattr(cfg, "ORCHESTRATION_ALLOW_ACP_WITH_SSH", True)
    assert cfg.effective_orchestration_mode() == "acp_first"


def test_effective_orchestration_mode_keeps_legacy_when_configured(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "ssh_tunnel")
    monkeypatch.setattr(cfg, "ORCHESTRATION_MODE", "legacy")
    monkeypatch.setattr(cfg, "ORCHESTRATION_ALLOW_ACP_WITH_SSH", False)
    assert cfg.effective_orchestration_mode() == "legacy"


def test_effective_orchestration_mode_keeps_acp_when_not_ssh_execution(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "agent")
    monkeypatch.setattr(cfg, "ORCHESTRATION_MODE", "acp_first")
    monkeypatch.setattr(cfg, "ORCHESTRATION_ALLOW_ACP_WITH_SSH", False)
    assert cfg.effective_orchestration_mode() == "acp_first"
