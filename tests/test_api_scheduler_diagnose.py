"""Scheduler diagnostics API tests."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.api import schemas
from skynet.api.routes import app_state, diagnose_scheduler, get_scheduler


class StubScheduler:
    async def diagnose_selection(self, execution_spec, fallback="local"):  # noqa: ARG002
        return {
            "selected_provider": "local",
            "fallback_used": False,
            "preselected_provider": None,
            "required_capabilities": ["run_tests"],
            "candidates": ["local", "docker"],
            "scores": [
                {
                    "provider": "local",
                    "total_score": 0.9,
                    "health_score": 1.0,
                    "load_score": 0.8,
                    "capability_score": 1.0,
                    "success_score": 0.7,
                    "latency_score": 0.6,
                }
            ],
        }


@pytest.mark.asyncio
async def test_get_scheduler_uninitialized() -> None:
    app_state.scheduler = None
    with pytest.raises(HTTPException) as exc_info:
        get_scheduler()
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_diagnose_scheduler_returns_scored_response() -> None:
    stub = StubScheduler()
    app_state.scheduler = stub

    request = schemas.SchedulerDiagnoseRequest(
        execution_spec={"steps": [{"action": "run_tests"}]},
        fallback="local",
    )

    response = await diagnose_scheduler(request=request, scheduler=stub)
    assert response.selected_provider == "local"
    assert response.fallback_used is False
    assert response.required_capabilities == ["run_tests"]
    assert len(response.scores) == 1
    assert response.scores[0].provider == "local"
