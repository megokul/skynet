"""Enforce the repo single-source-of-truth settings policy."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

ALLOWED_FILES = {
    "skynet/settings/loader.py",
    "openclaw-gateway/settings/loader.py",
    "openclaw-agent/settings/loader.py",
    "openclaw-gateway/live_settings.py",
    "scripts/ci/check_settings_policy.py",
    "scripts/ci/render_settings_env.py",
    "openclaw-gateway/tests/test_settings_loader.py",
}

FORBIDDEN_SNIPPETS = {
    re.compile(r"\bload_dotenv\s*\("): "Use the shared settings loader instead of loading .env files directly.",
    re.compile(r"\bdotenv_values\s*\("): "Use the shared settings loader instead of loading .env files directly.",
    re.compile(r"\byaml\.safe_load\s*\("): "Use the shared settings loader instead of parsing settings YAML directly.",
    re.compile(r"\byaml\.load\s*\("): "Use the shared settings loader instead of parsing settings YAML directly.",
    re.compile(r"os\.(?:environ\.get|getenv)\(\s*[\"']SKYNET_SETTINGS_FILE[\"']"): "Read settings override env vars only inside the shared settings loader.",
    re.compile(r"os\.(?:environ\.get|getenv)\(\s*[\"']SKYNET_LOCAL_SETTINGS_FILE[\"']"): "Read settings override env vars only inside the shared settings loader.",
    re.compile(r"os\.(?:environ\.get|getenv)\(\s*[\"']SKYNET_ENV_FILE[\"']"): "Read settings override env vars only inside the shared settings loader.",
    re.compile(r"os\.(?:environ\.get|getenv)\(\s*[\"']OPENCLAW_ENV_FILE[\"']"): "Read settings override env vars only inside the shared settings loader.",
    re.compile(r"os\.(?:environ\.get|getenv)\(\s*[\"']SKYNET_AGENT_SETTINGS_FILE[\"']"): "Read settings override env vars only inside the shared settings loader.",
    re.compile(r"os\.(?:environ\.get|getenv)\(\s*[\"']SKYNET_AGENT_LOCAL_SETTINGS_FILE[\"']"): "Read settings override env vars only inside the shared settings loader.",
    re.compile(r"os\.(?:environ\.get|getenv)\(\s*[\"']SKYNET_AGENT_ENV_FILE[\"']"): "Read settings override env vars only inside the shared settings loader.",
}


def _iter_python_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("venv/", "venv-agent/", ".pytest-", "__pycache__/", "skills/")):
            continue
        out.append(path)
    return out


def main() -> int:
    violations: list[str] = []
    for path in _iter_python_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in ALLOWED_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern, reason in FORBIDDEN_SNIPPETS.items():
            if pattern.search(text):
                violations.append(f"{rel}: {reason} Found `{pattern.pattern}`.")

    if violations:
        print("Settings policy violations detected:")
        for violation in violations:
            print(f"- {violation}")
        return 1

    print("Settings policy check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
