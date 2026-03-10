from __future__ import annotations

from pathlib import Path

from skynet.settings import loader as shared_loader
from settings.loader import SettingsLoader


def test_settings_loader_treats_local_override_path_as_local_layer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    (settings_dir / "settings.yaml").write_text(
        "BASE_ONLY: base\nSHARED: base\n",
        encoding="utf-8",
    )
    (settings_dir / "settings.local.yaml").write_text(
        "LOCAL_ONLY: local\nSHARED: local\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    monkeypatch.setenv("SKYNET_SETTINGS_FILE", str(settings_dir / "settings.local.yaml"))

    loader = SettingsLoader(settings_dir=settings_dir, env_file=env_file)

    assert loader.get_str("BASE_ONLY") == "base"
    assert loader.get_str("LOCAL_ONLY") == "local"
    assert loader.get_str("SHARED") == "local"


def test_shared_loader_resolves_gateway_component_root_layout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    (settings_dir / "settings.yaml").write_text(
        "SKYNET_E2E_LIVE: true\nSKYNET_LIVE_E2E_FLOW: telegram_real\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    monkeypatch.setattr(shared_loader, "ROOT", tmp_path)
    monkeypatch.delenv("SKYNET_SETTINGS_FILE", raising=False)
    monkeypatch.delenv("SKYNET_ENV_FILE", raising=False)
    monkeypatch.delenv("OPENCLAW_ENV_FILE", raising=False)
    monkeypatch.delenv("SKYNET_E2E_LIVE", raising=False)
    monkeypatch.delenv("SKYNET_LIVE_E2E_FLOW", raising=False)

    loader = shared_loader.SettingsLoader(
        component="gateway",
        repo_root=tmp_path,
        env_file=env_file,
    )

    assert loader.settings_file == settings_dir / "settings.yaml"
    assert loader.get_bool("SKYNET_E2E_LIVE") is True
    assert loader.get_str("SKYNET_LIVE_E2E_FLOW") == "telegram_real"
