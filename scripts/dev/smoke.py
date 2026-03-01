"""Run quick repository health checks without requiring make."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run(cmd: list[str]) -> None:
    """
    Run.
    
    Purpose:
    - Implement `run` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `cmd`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    """
    Main.
    
    Purpose:
    - Implement `main` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `int` when available; otherwise side effects only.
    """

    run([sys.executable, "scripts/ci/check_stale_paths.py"])
    run([sys.executable, "scripts/ci/check_control_plane_boundary.py"])
    run([sys.executable, "scripts/ci/check_engineering_policy.py"])
    run(
        [
            sys.executable,
            "-m",
            "py_compile",
            "skynet/api/main.py",
            "skynet/api/routes.py",
            "skynet/api/schemas.py",
            "scripts/dev/run_api.py",
            "scripts/manual/check_api.py",
            "scripts/manual/check_e2e_integration.py",
            "scripts/manual/check_skynet_delegate.py",
        ]
    )
    run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_api_lifespan.py",
            "tests/test_api_provider_config.py",
            "tests/test_api_control_plane.py",
            "tests/test_job_locking.py",
            "tests/test_worker_registry.py",
            "-q",
        ]
    )
    print("Smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
