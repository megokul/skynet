"""Run quick repository health checks without requiring make."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    run([sys.executable, "scripts/ci/check_stale_paths.py"])
    run(
        [
            sys.executable,
            "-m",
            "py_compile",
            "scripts/dev/run_api.py",
            "scripts/manual/check_api.py",
            "scripts/manual/check_e2e_integration.py",
            "scripts/manual/check_skynet_delegate.py",
        ]
    )
    run([sys.executable, "tests/test_dispatcher.py"])
    run([sys.executable, "-m", "pytest", "tests/test_api_lifespan.py", "-q"])
    print("Smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
