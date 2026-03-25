from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]

ROOT_TEST_FILES: tuple[str, ...] = (
    "tests/test_api_lifespan.py",
    "tests/test_api_provider_config.py",
    "tests/test_api_control_plane.py",
    "tests/test_job_locking.py",
    "tests/test_task_queue_control_plane.py",
    "tests/test_worker_registry.py",
    "tests/test_ci_engineering_policy.py",
    "tests/test_ci_repo_hygiene.py",
    "tests/test_project_documentation_skill.py",
    "tests/test_prompt_references.py",
)

DEFAULT_PYTEST_FLAGS: tuple[str, ...] = ("-q",)
ROOT_TEST_ENTRYPOINT = "python -m skynet.test_matrix --run"


def pytest_args(*, extra_args: Sequence[str] | None = None) -> list[str]:
    args = ["-m", "pytest", *ROOT_TEST_FILES, *DEFAULT_PYTEST_FLAGS]
    if extra_args:
        args.extend(str(item) for item in extra_args if str(item).strip())
    return args


def build_command(*, python_executable: str = "python", extra_args: Sequence[str] | None = None) -> str:
    return " ".join([python_executable, *pytest_args(extra_args=extra_args)])


ROOT_TEST_COMMAND = build_command()


def run_root_test_matrix(
    *,
    python_executable: str | None = None,
    extra_args: Sequence[str] | None = None,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    cmd = [python_executable or sys.executable, *pytest_args(extra_args=extra_args)]
    return subprocess.run(
        cmd,
        cwd=cwd or ROOT,
        text=True,
        check=check,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run or inspect the authoritative root test matrix.")
    parser.add_argument("--print-command", action="store_true", help="Print the equivalent pytest command.")
    parser.add_argument("--print-files", action="store_true", help="Print the curated root test files.")
    parser.add_argument(
        "--run",
        action="store_true",
        help="Run the authoritative root matrix. This is the default when no print flags are used.",
    )
    parser.add_argument(
        "pytest_args",
        nargs="*",
        help="Extra pytest arguments appended after the curated root matrix.",
    )
    args = parser.parse_args(argv)

    if args.print_files:
        for rel_path in ROOT_TEST_FILES:
            print(rel_path)
        return 0

    if args.print_command:
        print(build_command(extra_args=args.pytest_args))
        return 0

    run_root_test_matrix(extra_args=args.pytest_args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
