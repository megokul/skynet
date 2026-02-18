"""FastAPI lifespan integration tests."""

from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.api.main import app
from skynet.api.routes import app_state


def test_api_lifespan_initializes_shared_runtime(monkeypatch) -> None:
    # Keep startup deterministic and avoid external AI dependency during this test.
    monkeypatch.delenv("GOOGLE_AI_API_KEY", raising=False)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("SKYNET_EXECUTION_PROVIDER", "local")

    with TestClient(app) as client:
        response = client.get("/v1/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["components"]["policy_engine"] == "ok"

        assert app_state.provider_monitor is not None
        assert app_state.scheduler is not None
        assert app_state.execution_router is not None
        assert app_state.worker_registry is not None
        assert app_state.ledger_db is not None

    # Lifespan shutdown should clear app_state references.
    assert app_state.provider_monitor is None
    assert app_state.scheduler is None
    assert app_state.execution_router is None
    assert app_state.worker_registry is None
    assert app_state.ledger_db is None
