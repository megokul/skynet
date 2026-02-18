# SKYNET — Agent Guide

**For**: AI Coding Agents (Claude Code, Cline, Continue, etc.)
**Purpose**: Best practices for working on SKYNET project
**Last Updated**: 2026-02-16

> **🎯 GOAL**: Help AI agents understand SKYNET's architecture, conventions, and workflows.
> Read [CLAUDE.md](CLAUDE.md) for current implementation status.

---

## 🏗️ Architecture Overview

### Current Architecture (Session 016+)

SKYNET uses a **Control Plane vs Execution Plane** architecture:

```
Human → OpenClaw Gateway (Telegram) → SKYNET API (FastAPI)
                                            ↓
                                    Returns execution plan
                                            ↓
                         OpenClaw executes via workers
                                            ↓
                         Reports back to SKYNET API
```

**SKYNET (Control Plane)**:
- FastAPI service on port 8000
- Endpoints: /v1/plan, /v1/report, /v1/policy/check, /v1/health
- Responsibilities: Planning (AI), policy enforcement, governance

**OpenClaw (Execution Plane)**:
- User interface (Telegram/Slack/Web)
- Subagent orchestration (coder, tester, builder, deployer)
- Worker management (laptop SSH, EC2 Docker)

---

## 📁 Project Structure

```
e:\MyProjects\skynet/
  ├── venv/                    # Virtual environment
  ├── .env                     # Environment variables
  │
  ├── skynet/                  # Main Python package
  │   ├── api/                 # FastAPI service (NEW - Session 016)
  │   │   ├── main.py          # FastAPI app + lifespan
  │   │   ├── routes.py        # Endpoint handlers
  │   │   └── schemas.py       # Pydantic models
  │   │
  │   ├── core/                # Core components
  │   │   ├── planner.py       # AI-powered planning (Gemini)
  │   │   ├── dispatcher.py    # Plan → ExecutionSpec
  │   │   └── orchestrator.py  # Job lifecycle (legacy)
  │   │
  │   ├── policy/              # Safety & governance
  │   │   ├── engine.py        # Policy validation
  │   │   └── rules.py         # Risk classification
  │   │
  │   ├── chathan/             # Execution protocol
  │   │   ├── protocol/        # PlanSpec, ExecutionSpec
  │   │   └── providers/       # Execution providers
  │   │
  │   ├── ledger/              # State management
  │   ├── queue/               # Celery workers (legacy)
  │   ├── sentinel/            # Monitoring
  │   └── archive/             # Logs & artifacts
  │
  ├── tests/                   # Automated pytest suite
  ├── scripts/
  │   ├── dev/
  │   │   └── run_api.py       # API server startup
  │   └── manual/              # Manual integration checks
  │
  └── Documentation/
      ├── CLAUDE.md            # Project context (read first!)
      ├── TODO.md              # Task tracking
      ├── SESSION_NOTES.md     # Session history
      ├── AGENT_GUIDE.md       # This file
      └── DEVELOPMENT.md       # Code patterns
```

---

## 🔧 Development Workflow

### 1. Starting a Coding Session

```bash
# Read project context
cat CLAUDE.md

# Check what needs to be done
cat TODO.md

# Activate virtual environment
venv\Scripts\activate  # Windows

# Load environment variables
# (.env file is automatically loaded by python-dotenv)
```

### 2. Making Changes

**ALWAYS follow this sequence**:

1. **Read existing code** before modifying
2. **Understand the architecture** (control plane vs execution plane)
3. **Make minimal changes** (avoid over-engineering)
4. **Test your changes** (create test files)
5. **Update documentation** (mandatory 5-file rule - see below)

### 3. Testing

```bash
# Run specific test
python -m pytest tests/test_<component>.py -v

# Run FastAPI server for testing
python scripts/dev/run_api.py

# Test API endpoints
python scripts/manual/check_api.py
```

### 4. Documentation Updates (MANDATORY!)

After EVERY significant change, update these 5 files:

1. **CLAUDE.md** - Update implementation status, change log
2. **TODO.md** - Mark tasks complete, add new tasks
3. **SESSION_NOTES.md** - Add session entry with decisions/learnings
4. **AGENT_GUIDE.md** - Update if workflow changed
5. **DEVELOPMENT.md** - Update if patterns changed

See [POLICY.md](POLICY.md) for enforcement rules.

---

## 🎨 Code Style & Patterns

### Python Style

```python
# Type hints (Python 3.10+ style)
def func(name: str | None) -> dict[str, Any]:
    ...

# Async everywhere in FastAPI
async def endpoint() -> ResponseModel:
    result = await some_async_function()
    return result

# Pydantic models for data validation
class MyRequest(BaseModel):
    field: str = Field(..., description="Field description")
```

### FastAPI Patterns

```python
# Dependency injection
from fastapi import Depends

def get_service() -> Service:
    return app_state.service

@router.post("/endpoint")
async def endpoint(service: Service = Depends(get_service)):
    ...

# Lifespan management
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    initialize_components()
    yield
    # Shutdown
    cleanup()
```

### Error Handling

```python
# FastAPI exceptions
from fastapi import HTTPException

if not resource:
    raise HTTPException(status_code=404, detail="Not found")

# Logging
import logging
logger = logging.getLogger("skynet.component")
logger.info("Message")
logger.error("Error", exc_info=True)
```

---

## 🧪 Testing Guidelines

### Test File Naming

- Place automated tests in `tests/` as `test_<component>.py`
- Put manual service checks in `scripts/manual/check_<flow>.py`

### Test Structure

```python
"""Test <Component> - <Purpose>."""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from skynet.component import Component

async def test_basic_functionality():
    """Test basic functionality."""
    component = Component()
    result = await component.method()
    assert result is not None

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_basic_functionality())
```

### Running Tests

```bash
# Run single automated test
python -m pytest tests/test_component.py -v

# Run full automated test suite
python -m pytest tests/ -v
```

---

## 🚫 What NOT to Do

1. **Don't over-engineer** - Keep it simple
   - No unnecessary abstractions
   - No premature optimization
   - No feature creep

2. **Don't skip documentation** - MANDATORY 5-file update rule
   - Every significant change requires doc updates
   - See POLICY.md

3. **Don't break existing code** - Read before modifying
   - Understand existing patterns
   - Preserve backward compatibility when possible
   - Test thoroughly

4. **Don't add dependencies carelessly**
   - Check if dependency already exists
   - Use built-in libraries when possible
   - Document new dependencies

---

## 🔑 Key Concepts

### 1. Control Plane vs Execution Plane

**Control Plane (SKYNET)**:
- What: Planning, policy, governance
- How: FastAPI service
- Why: Centralized decision-making

**Execution Plane (OpenClaw)**:
- What: Task execution, user interface
- How: Subagents + workers
- Why: Distributed execution

### 2. Planning Flow

```
User message → OpenClaw
    ↓
OpenClaw calls POST /v1/plan
    ↓
SKYNET Planner (Gemini AI)
    ↓
Returns execution plan + approval gates
    ↓
OpenClaw executes plan
    ↓
OpenClaw calls POST /v1/report
```

### 3. Policy Enforcement

```
Action → Policy Engine
    ↓
Risk classification (LOW/MEDIUM/HIGH)
    ↓
Approval requirement check
    ↓
Allow/deny decision
```

---

## 🎯 Common Tasks

### Add a New API Endpoint

1. Define Pydantic schemas in `skynet/api/schemas.py`
2. Implement handler in `skynet/api/routes.py`
3. Add tests in `tests/test_api_*.py`
4. Update documentation

### Modify Existing Component

1. Read component code thoroughly
2. Understand dependencies and callers
3. Make minimal changes
4. Test edge cases
5. Update docs

### Fix a Bug

1. Reproduce the bug with a test
2. Identify root cause
3. Fix with minimal changes
4. Verify fix with test
5. Update docs if behavior changed

---

## 📚 Learning Resources

### Internal Documentation

- [CLAUDE.md](CLAUDE.md) - Current implementation status
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) - Original 8-phase plan
- [LEARNING_IMPLEMENTATION_PLAN.md](LEARNING_IMPLEMENTATION_PLAN.md) - Learning-focused guide
- [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) - Architectural decisions

### External References

- FastAPI: https://fastapi.tiangolo.com/
- Pydantic: https://docs.pydantic.dev/
- Google Gemini: https://ai.google.dev/gemini-api/docs

---

## 💡 Tips for AI Agents

1. **Always read CLAUDE.md first** - It contains current state and context
2. **Check TODO.md before starting** - Understand what needs to be done
3. **Use existing patterns** - Don't invent new ways of doing things
4. **Test as you go** - Don't write large chunks without testing
5. **Update docs immediately** - Don't batch documentation updates
6. **Ask clarifying questions** - Better to ask than assume

---

## 🆘 Troubleshooting

### "Module not found" errors

```bash
# Make sure virtual environment is activated
venv\Scripts\activate

# Install missing dependencies
pip install <package>
```

### "GOOGLE_AI_API_KEY not found"

```bash
# Check .env file exists
cat .env

# Load environment in Python
from dotenv import load_dotenv
load_dotenv()
```

### FastAPI server won't start

```bash
# Check port 8000 is not in use
netstat -ano | grep :8000

# Kill existing process
taskkill //PID <pid> //F

# Restart server
python scripts/dev/run_api.py
```

---

**Remember**: Quality over speed. Take time to understand before changing.

---

## Session 019 Notes (2026-02-18)

### What changed
- Scheduler placeholders were upgraded to real integrations:
  - `ProviderMonitor` health data is now consumed by scheduler scoring.
  - `WorkerRegistry`/DB active-job load now contributes to scheduler scoring.
  - `MemoryManager` task history now contributes success/latency scoring.
- Dispatcher boot wiring now enables `ProviderScheduler` by default (`skynet/main.py`).
- API route bug fix: imported `schemas` alias in `skynet/api/routes.py` for endpoints using `schemas.*`.

### How to validate quickly
- Run:
  - `python -m pytest tests/test_scheduler.py tests/test_dispatcher.py -q`

### Immediate engineering priorities
1. Inject a shared `ProviderMonitor` via app lifecycle, not ad hoc.
2. Inject shared `ExecutionRouter` in API routes (`/v1/execute`) instead of constructing per request.
3. Add diagnostics endpoint for scheduler scores.

---

## Session 020 Notes (2026-02-18)

### Completed in this session
- API direct execution endpoint now depends on shared app-state router (`get_execution_router`) instead of creating a new router per request.
- FastAPI lifespan now initializes and manages:
  - `ProviderMonitor`
  - `ProviderScheduler`
  - `ExecutionRouter`
- Planner import in routes is now type-check-only to prevent unnecessary runtime dependency failures when Gemini libs are unavailable.
- Added `tests/test_api_execute.py`.

### Quick verification
- `python -m pytest tests/test_api_execute.py tests/test_scheduler.py tests/test_dispatcher.py -q`

## Session 021 Notes (2026-02-18)

### Completed in this session
- Added scheduler diagnostics API endpoint: `POST /v1/scheduler/diagnose`.
- Added scheduler diagnostics method (`diagnose_selection`) that returns:
  - selected provider
  - fallback/preselected metadata
  - required capabilities
  - candidate providers
  - full score breakdown per provider
- Added tests:
  - `tests/test_api_scheduler_diagnose.py`
  - `tests/test_scheduler.py` diagnostics coverage

### Quick verification
- `python -m pytest tests/test_api_scheduler_diagnose.py tests/test_api_execute.py tests/test_scheduler.py tests/test_dispatcher.py -q`

## Session 022 Notes (2026-02-18)

### Completed in this session
- Added lifespan integration coverage via `tests/test_api_lifespan.py`.
- Confirmed FastAPI startup/shutdown correctly initializes and clears:
  - `app_state.provider_monitor`
  - `app_state.scheduler`
  - `app_state.execution_router`
- Updated `skynet/api/main.py` to lazy import Planner during startup only.

### Quick verification
- `python -m pytest tests/test_api_lifespan.py tests/test_api_scheduler_diagnose.py tests/test_api_execute.py tests/test_scheduler.py tests/test_dispatcher.py -q`

## Session 023 Notes (2026-02-18)

### Completed in this session
- Updated `README.md` with:
  - current control-plane endpoint list
  - concrete `/v1/scheduler/diagnose` example request/response

## Session 024 Notes (2026-02-18)

### Completed in this session
- API lifespan now initializes ledger DB + `WorkerRegistry`.
- Scheduler in API runtime now consumes `WorkerRegistry` for load-aware scoring.
- Lifespan tests now verify `worker_registry` and `ledger_db` startup/shutdown behavior.

## Session 025 Notes (2026-02-18)

### Completed in this session
- Added env-driven provider map builder for API runtime:
  - `SKYNET_MONITORED_PROVIDERS=local,mock,docker,ssh,chathan`
- Added provider config tests:
  - `tests/test_api_provider_config.py`
- Updated `.env.example` with provider monitoring/runtime keys.

## Session 026 Notes (2026-02-18)

### Completed in this session
- Added provider health dashboard endpoint:
  - `GET /v1/providers/health`
- Added endpoint tests:
  - `tests/test_api_provider_health.py`
- Updated API docs in `README.md` with request/response example.
