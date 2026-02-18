"""Ensure worker executes dispatcher-formatted execution specs (steps array)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from skynet.queue.worker import execute_job, shutdown_reliability_components


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["SKYNET_DB_PATH"] = str(Path(tmpdir) / "steps_format.db")
        os.environ["SKYNET_ALLOWED_PATHS"] = str(Path.cwd())
        os.environ["SKYNET_WORKER_ID"] = "worker-steps-format"

        execution_spec = {
            "job_id": "job-steps-format",
            "provider": "local",
            "steps": [
                {
                    "id": "s1",
                    "action": "git_status",
                    "params": {"working_dir": str(Path.cwd())},
                    "timeout_sec": 60,
                    "requires_approval": False,
                    "description": "Run git status",
                }
            ],
        }

        result = execute_job("job-steps-format", execution_spec)
        assert result["status"] in {"success", "partial_failure"}
        assert len(result["results"]) == 1
        assert result["results"][0]["action"] == "git_status"
        shutdown_reliability_components()

    print("[SUCCESS] Worker steps-format test passed")


if __name__ == "__main__":
    main()
