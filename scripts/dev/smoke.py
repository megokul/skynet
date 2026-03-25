"""Run the curated repo smoke matrix without requiring make."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skynet.test_matrix import run_root_test_matrix


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    run([sys.executable, "scripts/ci/check_stale_paths.py"])
    run([sys.executable, "scripts/ci/check_control_plane_boundary.py"])
    run([sys.executable, "scripts/ci/check_settings_policy.py"])
    run([sys.executable, "scripts/ci/check_repo_hygiene.py"])
    run([sys.executable, "scripts/ci/check_engineering_policy.py", "--mode", "baseline"])
    run(
        [
            sys.executable,
            "-m",
            "py_compile",
            "skynet/api/main.py",
            "skynet/api/routes.py",
            "skynet/api/schemas.py",
            "skynet/settings/loader.py",
            "openclaw-gateway/config.py",
            "openclaw-agent/config.py",
            "scripts/dev/run_api.py",
            "scripts/manual/check_api.py",
            "scripts/manual/check_e2e_integration.py",
            "scripts/manual/check_skynet_delegate.py",
            "scripts/ci/check_repo_hygiene.py",
        ]
    )
    print(f"$ {sys.executable} -m skynet.test_matrix --run")
    run_root_test_matrix(cwd=ROOT)
    print("Smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
