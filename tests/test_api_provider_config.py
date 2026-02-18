"""Provider config resolution tests for API startup."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.api.main import _build_providers_from_env


def test_build_providers_default_local_mock(monkeypatch) -> None:
    monkeypatch.delenv("SKYNET_MONITORED_PROVIDERS", raising=False)
    providers = _build_providers_from_env()
    assert "local" in providers
    assert "mock" in providers


def test_build_providers_respects_configured_subset(monkeypatch) -> None:
    monkeypatch.setenv("SKYNET_MONITORED_PROVIDERS", "mock")
    providers = _build_providers_from_env()
    assert list(providers.keys()) == ["mock"]


def test_build_providers_unknown_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("SKYNET_MONITORED_PROVIDERS", "unknown_provider")
    providers = _build_providers_from_env()
    assert "local" in providers
