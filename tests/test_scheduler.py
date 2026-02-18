"""ProviderScheduler tests."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.ledger.schema import init_db
from skynet.ledger.worker_registry import WorkerRegistry
from skynet.memory.models import MemoryRecord, MemoryType
from skynet.scheduler.scheduler import ProviderScheduler


class StubHealth:
    def __init__(self, status: str, latency_ms: float = 0.0, failures: int = 0):
        self.status = status
        self.latency_ms = latency_ms
        self.consecutive_failures = failures


class StubProviderMonitor:
    def __init__(self, health_map: dict[str, StubHealth]):
        self._health_map = health_map
        self.providers = {name: object() for name in health_map}

    def get_provider_health(self, provider_name: str):
        return self._health_map.get(provider_name)

    async def check_provider(self, provider_name: str, provider):  # noqa: ARG002
        return self._health_map.get(provider_name)


class StubMemoryStorage:
    def __init__(self, records: list[MemoryRecord]):
        self._records = records

    async def search_memories(self, memory_type=None, limit: int = 10, filters=None):  # noqa: ARG002
        if memory_type:
            records = [r for r in self._records if r.memory_type == memory_type]
        else:
            records = list(self._records)
        return records[:limit]


class StubMemoryManager:
    def __init__(self, records: list[MemoryRecord]):
        self.storage = StubMemoryStorage(records)


async def test_scheduler_provider_load_uses_worker_registry() -> None:
    db = await init_db(":memory:")
    registry = WorkerRegistry(db)

    # 2 active local jobs (busy + online with current_job_id)
    await db.execute(
        "INSERT INTO workers (id, provider_name, status, current_job_id, last_heartbeat) VALUES (?, ?, ?, ?, datetime('now'))",
        ("w1", "local", "busy", "job-1"),
    )
    await db.execute(
        "INSERT INTO workers (id, provider_name, status, current_job_id, last_heartbeat) VALUES (?, ?, ?, ?, datetime('now'))",
        ("w2", "local", "online", "job-2"),
    )
    await db.execute(
        "INSERT INTO workers (id, provider_name, status, current_job_id, last_heartbeat) VALUES (?, ?, ?, ?, datetime('now'))",
        ("w3", "docker", "online", None),
    )
    await db.commit()

    scheduler = ProviderScheduler(worker_registry=registry)
    assert await scheduler._get_provider_load("local") == 2
    assert await scheduler._get_provider_load("docker") == 0
    await db.close()


async def test_scheduler_provider_history_aggregates_memory() -> None:
    records = [
        MemoryRecord(
            memory_type=MemoryType.TASK_EXECUTION,
            content={"provider": "local", "success": True, "duration_seconds": 10},
        ),
        MemoryRecord(
            memory_type=MemoryType.TASK_EXECUTION,
            content={"provider": "local", "success": False, "duration_seconds": 20},
        ),
        MemoryRecord(
            memory_type=MemoryType.TASK_EXECUTION,
            content={"provider": "docker", "success": True, "duration_seconds": 30},
        ),
    ]

    scheduler = ProviderScheduler(memory_manager=StubMemoryManager(records))
    success, failure, avg_duration = await scheduler._get_provider_history("local")
    assert success == 1
    assert failure == 1
    assert avg_duration == 15.0


async def test_scheduler_selects_healthier_provider() -> None:
    monitor = StubProviderMonitor(
        {
            "local": StubHealth(status="healthy"),
            "docker": StubHealth(status="unhealthy", failures=3),
        }
    )

    scheduler = ProviderScheduler(
        provider_monitor=monitor,
        available_providers=["local", "docker"],
    )

    execution_spec = {
        "job_id": "job-1",
        "steps": [{"action": "run_tests"}],
    }

    provider = await scheduler.select_provider(execution_spec)
    assert provider == "local"


async def test_scheduler_diagnose_includes_scores_and_selection() -> None:
    monitor = StubProviderMonitor(
        {
            "local": StubHealth(status="healthy"),
            "docker": StubHealth(status="unhealthy", failures=2),
        }
    )
    scheduler = ProviderScheduler(
        provider_monitor=monitor,
        available_providers=["local", "docker"],
    )

    execution_spec = {
        "job_id": "job-2",
        "steps": [{"action": "run_tests"}],
    }

    result = await scheduler.diagnose_selection(execution_spec)
    assert result["selected_provider"] == "local"
    assert result["fallback_used"] is False
    assert len(result["scores"]) >= 1
    assert result["scores"][0]["provider"] == "local"
