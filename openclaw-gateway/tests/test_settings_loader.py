from __future__ import annotations

from pathlib import Path

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
