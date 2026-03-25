"""Fail on repo-hygiene drift that should never be committed."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skynet.test_matrix import ROOT_TEST_ENTRYPOINT

LEGACY_ROOT_TESTS = (
    "tests/test_commander_engine.py",
    "tests/test_gateway_agent_runs_artifacts.py",
    "tests/test_integration_conversation.py",
    "tests/test_orchestrator_inbox.py",
    "tests/test_orchestrator_invariants.py",
    "tests/test_orchestrator_write_gating.py",
    "tests/test_project_create_bootstrap_warning.py",
    "tests/test_project_doc_intake_formatting.py",
    "tests/test_telegram_nl_flow.py",
    "tests/test_trace_logger.py",
    "tests/test_user_profile_memory.py",
)

DISALLOWED_TRACKED_PATTERNS = (
    "openclaw-agent/logs/*.jsonl",
    "openclaw-agent/logs/*.log",
    "openclaw-gateway/logs/*",
    "openclaw-gateway/tests/.artifacts/*",
    "logs/*",
    ".tmp/*",
    ".pytest-qwen-probe/*",
    "tmp-probe-dir*",
    "tmp-probe-dir*/*",
    "openclaw-agent/MyProjectsskynetlogs/*",
)

TRACKED_ALLOWLIST = {
    "openclaw-agent/logs/.gitkeep",
}

REQUIRED_GITIGNORE_SNIPPETS = (
    ".pytest-qwen-probe/",
    ".pytest-tmp/",
    ".tmp/",
    "tmp-probe-dir*/",
    "openclaw-agent/MyProjectsskynetlogs/",
    "openclaw-gateway/tests/.artifacts/",
)

REQUIRED_MAKE_TARGETS = (
    "install-control-plane:",
    "install-gateway:",
    "install-agent:",
    "install-dev:",
    "install-all:",
    "test-control-plane:",
    "test-gateway:",
    "test-agent:",
    "test-policy:",
    "test-policy-strict:",
    "check-hygiene:",
)

REQUIRED_DOC_SNIPPETS = {
    "README.md": (
        ROOT_TEST_ENTRYPOINT,
        "make test-control-plane",
        "make test-gateway",
        "make test-agent",
        "make test-policy",
        "make test-policy-strict",
        "python scripts/ci/check_repo_hygiene.py",
    ),
    "docs/INDEX.md": (
        ROOT_TEST_ENTRYPOINT,
        "make test-control-plane",
        "make test-gateway",
        "make test-agent",
        "python scripts/ci/check_repo_hygiene.py",
    ),
    "docs/IMPLEMENTATION_GUIDE.md": (
        ROOT_TEST_ENTRYPOINT,
        "python scripts/ci/check_repo_hygiene.py",
    ),
    "docs/KNOWN_DRIFT_AND_TEST_MATRIX.md": (
        ROOT_TEST_ENTRYPOINT,
        "tests/test_task_queue_control_plane.py",
        "tests/test_ci_engineering_policy.py",
        "tests/test_ci_repo_hygiene.py",
        "tests/test_project_documentation_skill.py",
        "tests/test_prompt_references.py",
        "python scripts/ci/check_repo_hygiene.py",
    ),
    "tests/README.md": (
        "default pytest discovery does not recurse into `tests/`",
        "`make test-control-plane`",
        "`python -m skynet.test_matrix --run`",
        "`test_ci_repo_hygiene.py`",
    ),
}

BANNED_DOC_SNIPPETS = (
    "python -m pytest tests/ -v",
    "python -m pytest tests/ -q",
)

REQUIRED_PYTEST_SNIPPETS = (
    "testpaths = openclaw-gateway/tests openclaw-agent/tests",
    "norecursedirs =",
    ".pytest-qwen-probe",
    "openclaw-gateway/tests/.artifacts",
)

SCAN_FILES = tuple(REQUIRED_DOC_SNIPPETS) + ("Makefile", "pytest.ini")


def _git_ls_files(root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()]


def collect_hygiene_violations(root: Path | None = None, *, tracked_files: list[str] | None = None) -> list[str]:
    repo_root = root or ROOT
    violations: list[str] = []
    tracked = tracked_files if tracked_files is not None else _git_ls_files(repo_root)

    for rel_path in tracked:
        if rel_path in TRACKED_ALLOWLIST:
            continue
        if any(fnmatch(rel_path, pattern) for pattern in DISALLOWED_TRACKED_PATTERNS):
            violations.append(f"tracked generated artifact: {rel_path}")

    for rel_path in LEGACY_ROOT_TESTS:
        if (repo_root / rel_path).exists():
            violations.append(f"legacy root test still exists on disk: {rel_path}")

    gitignore_path = repo_root / ".gitignore"
    gitignore_text = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    for snippet in REQUIRED_GITIGNORE_SNIPPETS:
        if snippet not in gitignore_text:
            violations.append(f".gitignore missing required entry: {snippet}")

    makefile_path = repo_root / "Makefile"
    makefile_text = makefile_path.read_text(encoding="utf-8") if makefile_path.exists() else ""
    for target in REQUIRED_MAKE_TARGETS:
        if target not in makefile_text:
            violations.append(f"Makefile missing target: {target}")
    for snippet in BANNED_DOC_SNIPPETS:
        if snippet in makefile_text:
            violations.append(f"Makefile still uses non-authoritative root test sweep: {snippet}")

    for rel_path in SCAN_FILES:
        path = repo_root / rel_path
        if not path.exists():
            violations.append(f"missing required repo file: {rel_path}")
            continue
        text = path.read_text(encoding="utf-8")
        for snippet in BANNED_DOC_SNIPPETS:
            if snippet in text:
                violations.append(f"{rel_path} contains banned command: {snippet}")
        for legacy_test in LEGACY_ROOT_TESTS:
            if legacy_test in text:
                violations.append(f"{rel_path} references deleted legacy root test: {legacy_test}")

    for rel_path, snippets in REQUIRED_DOC_SNIPPETS.items():
        text = (repo_root / rel_path).read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in text:
                violations.append(f"{rel_path} missing authoritative snippet: {snippet}")

    pytest_text = (repo_root / "pytest.ini").read_text(encoding="utf-8")
    for snippet in REQUIRED_PYTEST_SNIPPETS:
        if snippet not in pytest_text:
            violations.append(f"pytest.ini missing required snippet: {snippet}")

    return violations


def main() -> int:
    violations = collect_hygiene_violations(ROOT)
    if not violations:
        print("Repo hygiene checks passed.")
        return 0

    print("Repo hygiene violations detected:")
    for violation in violations:
        print(f"- {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
