"""Provider health dashboard API tests."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.api.routes import (
    app_state,
    get_provider_monitor,
    provider_health_dashboard,
)


class StubProviderMonitor:
    def get_dashboard_data(self):
        return {
            "status": "degraded",
            "healthy_count": 1,
            "unhealthy_count": 1,
            "unknown_count": 0,
            "total_count": 2,
            "providers": {
                "local": {"status": "healthy", "message": "OK"},
                "docker": {"status": "unhealthy", "message": "daemon unavailable"},
            },
            "last_check": 1234567890.0,
            "history": [{"timestamp": 1234567890.0}],
        }


@pytest.mark.asyncio
async def test_get_provider_monitor_uninitialized() -> None:
    app_state.provider_monitor = None
    with pytest.raises(HTTPException) as exc_info:
        get_provider_monitor()
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_provider_health_dashboard_response() -> None:
    monitor = StubProviderMonitor()
    app_state.provider_monitor = monitor

    response = await provider_health_dashboard(provider_monitor=monitor)
    assert response.status == "degraded"
    assert response.total_count == 2
    assert "local" in response.providers
    assert "docker" in response.providers
