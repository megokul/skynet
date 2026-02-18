"""Worker reliability integration tests (locks + registry heartbeat)."""

from __future__ import annotations

import asyncio
import importlib
import os
import tempfile

from skynet.ledger.job_locking import JobLockManager
from skynet.ledger.schema import init_db


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "worker_test.db")
        os.environ["SKYNET_DB_PATH"] = db_path
        os.environ["SKYNET_WORKER_ID"] = "worker-test-1"

        # Import after env vars are set so worker uses this DB.
        worker = importlib.import_module("skynet.queue.worker")
        importlib.reload(worker)

        # Simulate a competing worker that already locked the job.
        async def prelock() -> None:
            db = await init_db(db_path)
            locks = JobLockManager(db)
            await locks.acquire_lock("job-locked", "other-worker")
            await db.close()

        asyncio.run(prelock())

        result = worker.execute_job(
            "job-locked",
            {
                "job_id": "job-locked",
                "provider": "mock",
                "actions": [{"action": "git_status", "params": {}}],
            },
        )
        assert result["status"] == "skipped"

        health = worker.health_check()
        assert health["status"] == "healthy"
        assert health["worker_id"] == "worker-test-1"
        worker.shutdown_reliability_components()

        print("[SUCCESS] Worker reliability tests passed")


if __name__ == "__main__":
    main()
