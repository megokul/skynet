"""Unit tests for scripts/ci/check_repo_hygiene.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from skynet.test_matrix import ROOT_TEST_ENTRYPOINT


def _load_hygiene_module():
    repo_root = Path(__file__).parent.parent
    module_path = repo_root / "scripts" / "ci" / "check_repo_hygiene.py"
    spec = importlib.util.spec_from_file_location("check_repo_hygiene", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load check_repo_hygiene.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_common_files(repo_root: Path) -> None:
    (repo_root / ".gitignore").write_text(
        "\n".join(
            [
                ".pytest-qwen-probe/",
                ".pytest-tmp/",
                ".tmp/",
                "tmp-probe-dir*/",
                "openclaw-agent/MyProjectsskynetlogs/",
                "openclaw-gateway/tests/.artifacts/",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / "README.md").write_text(
        "\n".join(
            [
                ROOT_TEST_ENTRYPOINT,
                "make test-control-plane",
                "make test-gateway",
                "make test-agent",
                "make test-policy",
                "make test-policy-strict",
                "python scripts/ci/check_repo_hygiene.py",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    docs = repo_root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "INDEX.md").write_text(
        "\n".join(
            [
                ROOT_TEST_ENTRYPOINT,
                "make test-control-plane",
                "make test-gateway",
                "make test-agent",
                "python scripts/ci/check_repo_hygiene.py",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (docs / "IMPLEMENTATION_GUIDE.md").write_text(
        "\n".join(
            [
                ROOT_TEST_ENTRYPOINT,
                "python scripts/ci/check_repo_hygiene.py",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (docs / "KNOWN_DRIFT_AND_TEST_MATRIX.md").write_text(
        "\n".join(
            [
                ROOT_TEST_ENTRYPOINT,
                "tests/test_task_queue_control_plane.py",
                "tests/test_ci_engineering_policy.py",
                "tests/test_ci_repo_hygiene.py",
                "tests/test_project_documentation_skill.py",
                "tests/test_prompt_references.py",
                "python scripts/ci/check_repo_hygiene.py",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    tests_dir = repo_root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "README.md").write_text(
        "\n".join(
            [
                "default pytest discovery does not recurse into `tests/`",
                "`make test-control-plane`",
                "`python -m skynet.test_matrix --run`",
                "`test_ci_repo_hygiene.py`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / "Makefile").write_text(
        "\n".join(
            [
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
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / "pytest.ini").write_text(
        "\n".join(
            [
                "testpaths = openclaw-gateway/tests openclaw-agent/tests",
                "norecursedirs =",
                "    .pytest-qwen-probe",
                "    openclaw-gateway/tests/.artifacts",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_repo_hygiene_passes_for_curated_surface(tmp_path: Path) -> None:
    hygiene = _load_hygiene_module()
    _write_common_files(tmp_path)

    tracked = ["openclaw-agent/logs/.gitkeep", "tests/test_api_control_plane.py"]
    violations = hygiene.collect_hygiene_violations(tmp_path, tracked_files=tracked)
    assert violations == []


def test_repo_hygiene_fails_for_tracked_runtime_artifact(tmp_path: Path) -> None:
    hygiene = _load_hygiene_module()
    _write_common_files(tmp_path)

    tracked = ["openclaw-agent/logs/audit.jsonl"]
    violations = hygiene.collect_hygiene_violations(tmp_path, tracked_files=tracked)
    assert any("tracked generated artifact" in item for item in violations)


def test_repo_hygiene_fails_for_legacy_root_test_reference(tmp_path: Path) -> None:
    hygiene = _load_hygiene_module()
    _write_common_files(tmp_path)
    (tmp_path / "README.md").write_text(
        "tests/test_trace_logger.py\npython scripts/ci/check_repo_hygiene.py\n",
        encoding="utf-8",
    )

    violations = hygiene.collect_hygiene_violations(tmp_path, tracked_files=[])
    assert any("deleted legacy root test" in item for item in violations)


def test_repo_hygiene_fails_when_legacy_root_test_exists(tmp_path: Path) -> None:
    hygiene = _load_hygiene_module()
    _write_common_files(tmp_path)
    legacy = tmp_path / "tests" / "test_trace_logger.py"
    legacy.write_text("pass\n", encoding="utf-8")

    violations = hygiene.collect_hygiene_violations(tmp_path, tracked_files=[])
    assert any("legacy root test still exists on disk" in item for item in violations)
