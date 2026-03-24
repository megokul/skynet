from __future__ import annotations

import pytest

from ssh_tunnel_config import load_ssh_executor_config
from ssh_tunnel_support import parse_roots, sanitize_powershell_output


def _set_base_ssh_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "ssh_tunnel")
    monkeypatch.setenv("OPENCLAW_SSH_FALLBACK_ENABLED", "1")
    monkeypatch.setenv("OPENCLAW_SSH_HOST", "example-host")
    monkeypatch.setenv("OPENCLAW_SSH_USER", "tester")


def test_load_ssh_executor_config_groups_runtime_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_ssh_env(monkeypatch)
    monkeypatch.setenv("OPENCLAW_SSH_ALLOWED_ROOTS", "C:/Work;D:/Repos")
    monkeypatch.setenv("OPENCLAW_CLINE_PROVIDER_PRIORITY", "openai,groq,openai,invalid")
    monkeypatch.setenv("SKYNET_CODEX_WRITE_MODE", "workspace_write")

    config = load_ssh_executor_config()

    assert config.enabled is True
    assert config.host == "example-host"
    assert config.username == "tester"
    assert config.allowed_roots == ["C:/Work", "D:/Repos"]
    assert config.cline_provider_priority == ["openai", "groq"]
    assert config.codex_write_mode == "workspace_write"


def test_load_ssh_executor_config_normalizes_invalid_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_ssh_env(monkeypatch)
    monkeypatch.setenv("SKYNET_CODEX_WRITE_MODE", "bad-mode")
    monkeypatch.setenv("OPENCLAW_SSH_CLAUDE_PERMISSION_MODE", "bad-mode")

    config = load_ssh_executor_config()

    assert config.codex_write_mode == "danger_full_access"
    assert config.claude_permission_mode == "bypassPermissions"


def test_parse_roots_uses_safe_os_defaults() -> None:
    assert parse_roots("", "windows") == [r"%USERPROFILE%\Projects", r"%USERPROFILE%\Documents"]
    assert parse_roots("", "linux") == ["/home", "/tmp"]


def test_sanitize_powershell_output_extracts_clixml_payloads() -> None:
    raw = '<Objs Version="1.1.0.1"><S S="Error">broken</S><S S="Warning">warn</S></Objs>'

    assert sanitize_powershell_output(raw) == "broken\nwarn"
