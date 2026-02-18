# SKYNET — Development Guide

**For**: Developers and AI coding agents
**Purpose**: Code patterns, conventions, and best practices
**Last Updated**: 2026-02-16

> **📘 REFERENCE**: This document defines coding standards for SKYNET project.
> Read [AGENT_GUIDE.md](AGENT_GUIDE.md) for workflow and [CLAUDE.md](CLAUDE.md) for architecture.

---

## 🎨 Code Style

### Python Version

- **Minimum**: Python 3.11
- **Recommended**: Python 3.13 (current)
- **Features**: Use modern type hints (`str | None`, `dict[str, Any]`)

### Type Hints

```python
# Modern union syntax (Python 3.10+)
def func(name: str | None = None) -> dict[str, Any]:
    return {"name": name}

# Generic types
from typing import Any, Callable
from collections.abc import Sequence

def process(items: Sequence[str]) -> list[int]:
    return [len(item) for item in items]

# Forward references for circular dependencies
from __future__ import annotations

class Node:
    def add_child(self, child: Node) -> None:
        ...
```

### Async/Await

```python
# Prefer async def for I/O operations
async def fetch_data(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return response.json()

# Use asyncio.run() for top-level entry
if __name__ == "__main__":
    import asyncio
    result = asyncio.run(main())
```

### Imports

```python
# Standard library first
from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path

# Third-party packages
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Local imports (absolute paths)
from skynet.core.planner import Planner
from skynet.policy.engine import PolicyEngine
```

### Logging

```python
import logging

# Create logger for each module
logger = logging.getLogger("skynet.component.subcomponent")

# Use appropriate levels
logger.debug("Detailed diagnostic info")
logger.info("Normal operation info")
logger.warning("Warning about potential issues")
logger.error("Error occurred", exc_info=True)  # Include traceback

# Format: "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
```

---

## 🏗️ FastAPI Patterns

### Application Structure

```python
# main.py - FastAPI app with lifespan
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up...")
    initialize_components()
    yield
    # Shutdown
    logger.info("Shutting down...")
    cleanup()

app = FastAPI(
    title="Service Name",
    description="Service description",
    version="1.0.0",
    lifespan=lifespan,
)

# Include routers
from skynet.api.routes import router
app.include_router(router)
```

### Dependency Injection

```python
# routes.py - Use dependency injection for services
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/v1", tags=["api"])

# Application state container
class AppState:
    planner: Planner | None = None
    policy_engine: PolicyEngine | None = None

app_state = AppState()

# Dependency function
def get_planner() -> Planner:
    if app_state.planner is None:
        raise HTTPException(status_code=503, detail="Planner not initialized")
    return app_state.planner

# Use in endpoints
@router.post("/plan")
async def create_plan(
    request: PlanRequest,
    planner: Planner = Depends(get_planner),
) -> PlanResponse:
    plan = await planner.generate_plan(...)
    return PlanResponse(...)
```

### Pydantic Models

```python
# schemas.py - Request/response models
from pydantic import BaseModel, Field
from enum import Enum

class RiskLevel(str, Enum):
    """Risk classification."""
    LOW = "low"
    MEDIUM = "med"
    HIGH = "high"

class PlanRequest(BaseModel):
    """Request to generate a plan."""
    user_message: str = Field(..., description="User's task description")
    context: dict[str, Any] = Field(default_factory=dict)

class PlanResponse(BaseModel):
    """Response with execution plan."""
    plan_id: str
    steps: list[ExecutionStep]
    risk_level: RiskLevel
```

### Error Handling

```python
from fastapi import HTTPException

# Validation errors (400)
if not valid_input:
    raise HTTPException(status_code=400, detail="Invalid input")

# Not found (404)
if not resource:
    raise HTTPException(status_code=404, detail="Resource not found")

# Service unavailable (503)
if not service_ready:
    raise HTTPException(status_code=503, detail="Service not ready")

# Internal errors (500) - let FastAPI handle
try:
    result = dangerous_operation()
except Exception as e:
    logger.error(f"Operation failed: {e}", exc_info=True)
    raise HTTPException(status_code=500, detail=str(e))
```

---

## 🧪 Testing Patterns

### Test File Structure

```python
"""
Test <Component> - <Purpose>.

Tests:
1. Basic functionality
2. Error cases
3. Edge cases
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from skynet.component import Component

async def test_basic():
    """Test basic functionality."""
    component = Component()
    result = await component.method()
    assert result is not None
    print("✓ Basic test passed")

async def test_error():
    """Test error handling."""
    component = Component()
    try:
        await component.invalid_method()
        assert False, "Should have raised error"
    except ValueError:
        print("✓ Error test passed")

async def main():
    """Run all tests."""
    await test_basic()
    await test_error()
    print("\nAll tests passed!")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

### Test Assertions

```python
# Use descriptive assertions
assert result is not None, "Result should not be None"
assert len(items) == 3, f"Expected 3 items, got {len(items)}"
assert status == "ok", f"Expected 'ok', got '{status}'"

# Test exceptions
import pytest

@pytest.mark.asyncio
async def test_exception():
    with pytest.raises(ValueError, match="Invalid input"):
        await func(invalid_input)
```

### Mocking

```python
from unittest.mock import Mock, AsyncMock, patch

# Mock async function
async def test_with_mock():
    mock_client = AsyncMock()
    mock_client.generate_content.return_value = Mock(text="result")

    planner = Planner(client=mock_client)
    result = await planner.generate_plan("task")

    assert result is not None
```

---

## 📦 Component Patterns

### Service Classes

```python
class ServiceName:
    """
    Service description.

    Example:
        service = ServiceName(config)
        result = await service.process(data)
    """

    def __init__(self, config: dict[str, Any]):
        """
        Initialize service.

        Args:
            config: Service configuration
        """
        self.config = config
        self.logger = logging.getLogger(f"skynet.{self.__class__.__name__}")
        self.logger.info("Service initialized")

    async def process(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Process data.

        Args:
            data: Input data

        Returns:
            Processed result

        Raises:
            ValueError: If data is invalid
        """
        if not data:
            raise ValueError("Data cannot be empty")

        self.logger.debug(f"Processing: {data}")
        result = await self._internal_process(data)
        self.logger.info("Processing complete")

        return result

    async def _internal_process(self, data: dict[str, Any]) -> dict[str, Any]:
        """Internal processing logic."""
        # Implementation
        return data
```

### Dataclasses vs Pydantic

```python
# Use Pydantic for API models (validation + serialization)
from pydantic import BaseModel, Field, field_validator

class APIModel(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    count: int = Field(..., ge=0)

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v.isalnum():
            raise ValueError("Name must be alphanumeric")
        return v

# Use dataclasses for internal data structures
from dataclasses import dataclass, field

@dataclass
class InternalData:
    name: str
    items: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
```

---

## 🔒 Security Patterns

### Input Validation

```python
# Always validate user input
from pydantic import field_validator

class UserInput(BaseModel):
    command: str

    @field_validator('command')
    @classmethod
    def validate_command(cls, v: str) -> str:
        # Whitelist allowed commands
        allowed = ['git_status', 'list_files', 'run_tests']
        if v not in allowed:
            raise ValueError(f"Command '{v}' not allowed")
        return v
```

### Path Traversal Prevention

```python
from pathlib import Path

def safe_path(base: Path, user_path: str) -> Path:
    """Ensure path stays within base directory."""
    full_path = (base / user_path).resolve()
    if not str(full_path).startswith(str(base.resolve())):
        raise ValueError("Path traversal detected")
    return full_path
```

### Command Injection Prevention

```python
import shlex
import subprocess

# NEVER use shell=True with user input
# BAD:
subprocess.run(f"ls {user_input}", shell=True)  # Vulnerable!

# GOOD:
subprocess.run(['ls', user_input], shell=False)

# Or use shlex.quote for shell commands
import shlex
cmd = f"ls {shlex.quote(user_input)}"
subprocess.run(cmd, shell=True)
```

---

## 🎯 AI Integration Patterns

### Gemini API Usage

```python
from google import genai
from google.genai import types

class AIService:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-2.5-flash"

    async def generate(self, prompt: str) -> str:
        """Generate AI response."""
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=8192,
                    temperature=0.7,
                ),
            )
            return response.text
        except Exception as e:
            self.logger.error(f"AI generation failed: {e}")
            raise
```

### JSON Parsing from AI Output

```python
import json
import re

def extract_json(text: str) -> dict[str, Any]:
    """Extract JSON from AI response (handles markdown code blocks)."""
    # Try direct parsing first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Extract from markdown code block
    pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return json.loads(match.group(1))

    # Extract first JSON object
    pattern = r'\{.*?\}'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return json.loads(match.group(0))

    raise ValueError("No JSON found in response")
```

---

## 📊 Error Handling Best Practices

### Exception Hierarchy

```python
# Define custom exceptions
class SkynetError(Exception):
    """Base exception for SKYNET."""
    pass

class PlanningError(SkynetError):
    """Error during plan generation."""
    pass

class PolicyViolation(SkynetError):
    """Action violates policy."""
    pass

# Use specific exceptions
def validate(action: str) -> None:
    if is_blocked(action):
        raise PolicyViolation(f"Action '{action}' is blocked")
```

### Error Context

```python
# Add context to errors
try:
    result = process_task(task_id)
except Exception as e:
    logger.error(f"Task {task_id} failed: {e}", exc_info=True)
    raise RuntimeError(f"Processing failed for task {task_id}") from e
```

---

## 🚀 Performance Patterns

### Async Concurrency

```python
import asyncio

# Run multiple tasks concurrently
async def fetch_all(urls: list[str]) -> list[dict]:
    tasks = [fetch_url(url) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if not isinstance(r, Exception)]
```

### Caching

```python
from functools import lru_cache

@lru_cache(maxsize=128)
def expensive_operation(param: str) -> str:
    """Cached expensive operation."""
    # Heavy computation
    return result
```

---

## 📝 Documentation Standards

### Docstrings (Google Style)

```python
def complex_function(
    param1: str,
    param2: int,
    param3: list[str] | None = None,
) -> dict[str, Any]:
    """
    One-line summary of function.

    Longer description explaining what the function does,
    when to use it, and any important details.

    Args:
        param1: Description of param1
        param2: Description of param2
        param3: Optional param3 description

    Returns:
        Description of return value and its structure

    Raises:
        ValueError: When input is invalid
        RuntimeError: When operation fails

    Example:
        >>> result = complex_function("test", 42)
        >>> print(result['status'])
        'ok'
    """
    if not param1:
        raise ValueError("param1 cannot be empty")

    return {"status": "ok", "data": param1}
```

---

## 🎯 Project-Specific Patterns

### SKYNET API Client Pattern

```python
import httpx

class SkynetClient:
    """Client for SKYNET control plane API."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def generate_plan(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate execution plan."""
        response = await self.client.post(
            "/v1/plan",
            json={
                "request_id": str(uuid4()),
                "user_message": user_message,
                "context": context or {},
            },
        )
        response.raise_for_status()
        return response.json()

    async def close(self):
        """Close client."""
        await self.client.aclose()
```

---

**Remember**: Write code that is easy to read, maintain, and test. Clarity over cleverness.

---

## Session 019 Engineering Addendum (2026-02-18)

### Scheduler integration pattern
- `ProviderScheduler` should consume live signals from three sources:
  - Health: `ProviderMonitor.get_provider_health(...)` with on-demand `check_provider(...)` fallback.
  - Load: query `workers` table for active jobs (`status='busy'` or `status='online' AND current_job_id IS NOT NULL`).
  - History: aggregate `MemoryType.TASK_EXECUTION` records by provider for success/failure/avg duration.

### Wiring guideline
- Keep dispatcher provider selection logic centralized.
- Initialize dispatcher with scheduler in app bootstrap (`skynet/main.py`) and avoid scattered provider decisions.

### Test guideline
- Add focused scheduler unit tests for:
  - health-aware selection
  - load-aware score inputs
  - history aggregation correctness
- Reference implementation: `tests/test_scheduler.py`.

### API dependency injection guideline
- Prefer shared lifespan-managed dependencies for runtime services.
- `/v1/execute` should receive `ExecutionRouter` via dependency injection (`Depends`) instead of constructing per request.
- Keep route modules import-light:
  - expensive provider/AI imports should be delayed or moved behind type-checking when only used for annotations.

### Direct execution test pattern
- Use stub router objects to assert endpoint wiring behavior without requiring provider/network setup.
- Reference implementation: `tests/test_api_execute.py`.

### Scheduler diagnostics pattern
- Keep selection logic and observability coupled in scheduler module:
  - `select_provider(...)` for runtime decision
  - `diagnose_selection(...)` for debugging/inspection
- Expose diagnostics via API with typed request/response models.
- Include full factor breakdown (`health`, `load`, `capability`, `success`, `latency`) for explainability.
- Reference implementations:
  - `skynet/scheduler/scheduler.py`
  - `skynet/api/routes.py` (`/v1/scheduler/diagnose`)
  - `tests/test_api_scheduler_diagnose.py`

### Lifespan integration test pattern
- Use `fastapi.testclient.TestClient(app)` context manager to exercise startup/shutdown hooks.
- Assert runtime dependencies are initialized inside the context and cleared after exit.
- Keep startup deterministic in tests:
  - unset `GOOGLE_AI_API_KEY` if planner initialization is not under test
  - force `EMBEDDING_PROVIDER=mock`
- Reference implementation: `tests/test_api_lifespan.py`.

### API docs maintenance pattern
- Whenever adding an endpoint, update top-level `README.md` with:
  - endpoint path and purpose
  - minimal request/response example for non-trivial endpoints
- Current reference example: `/v1/scheduler/diagnose` in `README.md`.

### Scheduler load wiring pattern
- For API runtime scheduler quality, inject `WorkerRegistry` from a shared lifespan-managed ledger DB connection.
- Startup:
  - `app_state.ledger_db = await init_db(...)`
  - `app_state.worker_registry = WorkerRegistry(app_state.ledger_db)`
  - pass `worker_registry` into `ProviderScheduler(...)`
- Shutdown:
  - close `ledger_db`
  - clear `worker_registry` and `ledger_db` references from app state

### Provider map configuration pattern
- Build monitored providers from env in one place (`_build_providers_from_env()`).
- Use `SKYNET_MONITORED_PROVIDERS` as a comma-separated allowlist.
- Initialization rules:
  - unknown names: log warning and skip
  - provider init error: log warning and skip
  - empty final map: fallback to `local`
- Keep provider-specific env keys explicit in `.env.example`.

### Provider dashboard endpoint pattern
- Expose provider health through API using the shared `ProviderMonitor` instance.
- Add a dependency guard that returns 503 when provider monitor is unavailable.
- Use typed response model (`ProviderHealthDashboardResponse`) but keep nested provider details flexible (`dict[str, Any]`) because health payloads vary by provider.
- Reference implementation:
  - `skynet/api/routes.py` (`GET /v1/providers/health`)
  - `tests/test_api_provider_health.py`
