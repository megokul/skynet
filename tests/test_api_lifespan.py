"""FastAPI lifespan integration tests."""

from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.api.main import app
from skynet.api.routes import app_state


def test_api_lifespan_initializes_control_plane(monkeypatch) -> None:
    """
    Test scenario `test_api_lifespan_initializes_control_plane`.
    
    Purpose:
    - Implement `test_api_lifespan_initializes_control_plane` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `monkeypatch`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    monkeypatch.delenv("OPENCLAW_GATEWAY_URLS", raising=False)
    monkeypatch.setenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:8766")

    with TestClient(app) as client:
        response = client.get("/v1/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["components"]["control_registry"] == "ok"
        assert payload["components"]["gateway_client"] == "ok"

        assert app_state.control_registry is not None
        assert app_state.gateway_client is not None
        assert app_state.worker_registry is not None
        assert app_state.ledger_db is not None
        assert app_state.task_queue is not None

    # Lifespan shutdown should clear app_state references.
    assert app_state.control_registry is None
    assert app_state.gateway_client is None
    assert app_state.worker_registry is None
    assert app_state.ledger_db is None
    assert app_state.task_queue is None
    assert app_state.stale_lock_reaper is None
