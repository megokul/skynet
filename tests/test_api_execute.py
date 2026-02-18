"""API execute endpoint tests."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.api import schemas
from skynet.api.routes import app_state, execute_direct, get_execution_router


class StubExecutionRouter:
    def __init__(self):
        self.called = False
        self.execution_spec = None
        self.total_timeout = None

    async def execute_plan(self, execution_spec, total_timeout=None):
        self.called = True
        self.execution_spec = execution_spec
        self.total_timeout = total_timeout
        return {
            "job_id": "job-123",
            "status": "success",
            "provider": "mock",
            "results": [
                {
                    "action": "list_directory",
                    "status": "success",
                    "output": "ok",
                    "stdout": "ok",
                    "stderr": "",
                    "error": None,
                }
            ],
            "steps_completed": 1,
            "steps_total": 1,
            "elapsed_seconds": 0.2,
        }


@pytest.mark.asyncio
async def test_get_execution_router_uninitialized() -> None:
    app_state.execution_router = None
    with pytest.raises(HTTPException) as exc_info:
        get_execution_router()
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_execute_direct_uses_shared_execution_router() -> None:
    stub = StubExecutionRouter()
    app_state.execution_router = stub

    request = schemas.ExecuteRequest(
        execution_spec={
            "job_id": "job-123",
            "provider": "mock",
            "steps": [
                {"action": "list_directory", "params": {"directory": "."}},
            ],
        },
        timeout=30,
    )

    response = await execute_direct(request=request, execution_router=stub)

    assert stub.called is True
    assert stub.execution_spec["job_id"] == "job-123"
    assert stub.total_timeout == 30
    assert response.job_id == "job-123"
    assert response.status == "success"
    assert response.provider == "mock"
    assert len(response.results) == 1
    assert response.results[0].action == "list_directory"
