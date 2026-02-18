# SKYNET — Development Guide

**Purpose**: Code patterns, conventions, and best practices
**For**: All developers and AI coding agents
**Last Updated**: 2026-02-16

---

## 📐 Code Style & Conventions

### **File Naming**

```
✅ Good:
skynet/core/planner.py
skynet/core/dispatcher.py
test_planner.py

❌ Bad:
Planner.py
PlannerComponent.py
TestPlanner.py
```

### **Class Naming**

```python
✅ Good:
class Planner:
class ExecutionEngine:
class JobLockManager:

❌ Bad:
class planner:
class execution_engine:
class jobLockManager:
```

### **Function Naming**

```python
✅ Good:
def generate_plan(...)
def _build_prompt(...)  # Private
async def execute_job(...)  # Async

❌ Bad:
def GeneratePlan(...)
def buildPrompt(...)
def execute_Job(...)
```

### **Constant Naming**

```python
✅ Good:
MAX_RETRIES = 3
DEFAULT_TIMEOUT = 300
RISK_LEVELS = {"READ_ONLY": 0, "WRITE": 1}

❌ Bad:
maxRetries = 3
default_timeout = 300
riskLevels = {}
```

---

## 🎯 Type Hints (REQUIRED)

### **Always Use Type Hints**

```python
✅ Good:
def generate_plan(
    self,
    job_id: str,
    user_intent: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ...

async def execute(self, spec: ExecutionSpec) -> ExecutionResult:
    ...

❌ Bad:
def generate_plan(self, job_id, user_intent, context=None):
    ...
```

### **Modern Type Syntax (Python 3.10+)**

```python
✅ Good (Python 3.10+):
str | None
dict[str, Any]
list[str]

❌ Old Style (Don't use):
Optional[str]
Dict[str, Any]
List[str]
```

---

## 📝 Docstrings (REQUIRED)

### **Module Docstrings**

```python
"""
SKYNET Core — Planner

Converts user intent into structured PlanSpec using Gemini AI.
This is the first step in the job lifecycle.
"""
```

### **Class Docstrings**

```python
class Planner:
    """
    Uses Gemini AI to decompose user intent into a structured plan.

    The Planner is responsible for converting natural language task
    descriptions into formal PlanSpec objects that can be reviewed
    and approved by users before execution.

    Example:
        planner = Planner(api_key="your_key")
        plan = await planner.generate_plan(
            job_id="job_001",
            user_intent="Deploy the bot to production"
        )
    """
```

### **Function Docstrings**

```python
async def generate_plan(
    self,
    job_id: str,
    user_intent: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Generate a structured plan from user intent.

    Args:
        job_id: Unique job identifier
        user_intent: User's task description in natural language
        context: Optional context (working_dir, tech_stack, etc.)

    Returns:
        PlanSpec dict with steps, risk levels, and artifacts

    Raises:
        ValueError: If AI returns invalid JSON
        APIError: If Gemini API call fails
    """
```

---

## 🪵 Logging (REQUIRED)

### **Setup Logger**

```python
import logging

logger = logging.getLogger("skynet.core.planner")
# Pattern: "skynet.<module>.<component>"
```

### **Log Levels**

```python
logger.debug("Detailed info for debugging")
logger.info("Normal operation info")
logger.warning("Something unexpected but handled")
logger.error("Error that prevented operation")
```

### **Good Logging Examples**

```python
✅ Good:
logger.info(f"Planner initialized with Gemini {model}")
logger.info(f"Generating plan for: {user_intent[:50]}...")
logger.debug(f"Gemini response: {len(text)} chars")
logger.error(f"Gemini API error: {e}")

❌ Bad:
print("Starting planner")
logger.info("Error!")  # Not specific
logger.debug(huge_data_dump)  # Too much data
```

---

## 🔄 Async Patterns

### **Always Use Async for I/O**

```python
✅ Good:
async def generate_plan(...) -> dict:
    response = await self.client.aio.models.generate_content(...)
    return response

❌ Bad:
def generate_plan(...) -> dict:
    response = self.client.models.generate_content(...)  # Blocking!
    return response
```

### **Async Context Managers**

```python
✅ Good:
async with aiohttp.ClientSession() as session:
    async with session.get(url) as response:
        data = await response.json()

❌ Bad:
with requests.get(url) as response:  # Blocking!
    data = response.json()
```

---

## 🧪 Testing Patterns

### **Test File Location**

```
✅ Good:
e:\MyProjects\skynet\test_planner.py
e:\MyProjects\skynet\test_dispatcher.py

❌ Bad:
e:\MyProjects\skynet\skynet\tests\test_planner.py
```

### **Test File Structure**

```python
"""Test the Planner with Gemini."""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add skynet to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from skynet.core.planner import Planner

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)

# Load .env
load_dotenv()


async def main():
    print("=" * 60)
    print("Component Test")
    print("=" * 60)

    # ... test logic ...


if __name__ == "__main__":
    asyncio.run(main())
```

### **Test Output (Windows Compatible)**

```python
✅ Good (ASCII only):
print("[SUCCESS] Plan Generated!")
print("  - Step 1: Navigate to directory")
print("  - Step 2: Execute command")

❌ Bad (Emojis cause encoding errors):
print("✅ Plan Generated!")
print("🔧 Steps:")
```

---

## 🏗️ Class Structure Patterns

### **Standard Class Template**

```python
"""
SKYNET Core — Component Name

Brief description of what this component does.
"""

from __future__ import annotations

import logging
import os
from typing import Any

# Local imports
from skynet.shared.errors import SkynetError
from skynet.policy.engine import PolicyEngine

logger = logging.getLogger("skynet.core.component")


class Component:
    """
    One-line description.

    Detailed description of the component's purpose,
    responsibilities, and how it fits in the architecture.

    Example:
        component = Component(dependencies...)
        result = await component.do_something(...)
    """

    def __init__(self, dependency: SomeType):
        """
        Initialize the component.

        Args:
            dependency: Description of the dependency
        """
        self.dependency = dependency
        logger.info("Component initialized")

    async def public_method(self, param: str) -> dict[str, Any]:
        """
        Public method description.

        Args:
            param: Description

        Returns:
            Description of return value
        """
        result = await self._private_method(param)
        return result

    async def _private_method(self, param: str) -> str:
        """Private helper method."""
        # Implementation
        return processed_param
```

---

## 🔐 Error Handling

### **Specific Exceptions**

```python
✅ Good:
try:
    response = await api_call()
except APIError as e:
    logger.error(f"API error: {e}")
    raise
except ValueError as e:
    logger.error(f"Invalid data: {e}")
    return default_value

❌ Bad:
try:
    response = await api_call()
except Exception as e:  # Too broad!
    pass  # Silently fails!
```

### **Custom Exceptions**

```python
# skynet/shared/errors.py

class SkynetError(Exception):
    """Base exception for SKYNET."""

class PlanningError(SkynetError):
    """Error during plan generation."""

class ValidationError(SkynetError):
    """Error during validation."""
```

---

## 📦 Imports Organization

### **Order of Imports**

```python
"""Module docstring."""

# 1. Future imports
from __future__ import annotations

# 2. Standard library
import json
import logging
import os
import re
from typing import Any

# 3. Third-party
import aiohttp
from google import genai

# 4. Local imports - absolute
from skynet.shared.errors import SkynetError
from skynet.policy.engine import PolicyEngine
from skynet.chathan.protocol.plan_spec import PlanSpec

# Setup logger AFTER imports
logger = logging.getLogger("skynet.module")
```

---

## 🎨 Code Formatting

### **Line Length**

- **Maximum**: 100 characters (soft limit)
- **Preferred**: 80-90 characters
- **Break long lines** at logical points

```python
✅ Good:
result = await self.client.aio.models.generate_content(
    model=self.model_name,
    contents=prompt,
    config=types.GenerateContentConfig(
        max_output_tokens=8192,
        temperature=0.7,
    ),
)

❌ Bad:
result = await self.client.aio.models.generate_content(model=self.model_name, contents=prompt, config=types.GenerateContentConfig(max_output_tokens=8192, temperature=0.7))
```

### **Whitespace**

```python
✅ Good:
def func(a: int, b: str) -> dict[str, Any]:
    result = {"key": "value"}
    return result

❌ Bad:
def func(a:int,b:str)->dict[str,Any]:
    result={"key":"value"}
    return result
```

---

## 🗂️ File Organization

### **Module Structure**

```python
"""Module docstring."""

# Imports
...

# Constants
MAX_RETRIES = 3
DEFAULT_TIMEOUT = 300

# Logger
logger = logging.getLogger("skynet.module")

# Helper functions (private)
def _helper_function():
    ...

# Main classes
class MainClass:
    ...

# Public functions
def public_function():
    ...
```

---

## 🔗 Dependency Injection Pattern

### **Constructor Injection (Preferred)**

```python
class Orchestrator:
    def __init__(
        self,
        planner: Planner,
        dispatcher: Dispatcher,
        policy_engine: PolicyEngine,
        ledger: Ledger,
    ):
        """Inject dependencies via constructor."""
        self.planner = planner
        self.dispatcher = dispatcher
        self.policy = policy_engine
        self.ledger = ledger
```

### **Factory Pattern**

```python
# skynet/main.py

async def build_orchestrator() -> Orchestrator:
    """Factory function to wire dependencies."""
    # Initialize components
    ledger = await init_ledger()
    policy = PolicyEngine()
    planner = Planner(ai_client, policy)
    dispatcher = Dispatcher(policy, queue, ledger)

    # Wire together
    orchestrator = Orchestrator(planner, dispatcher, policy, ledger)

    return orchestrator
```

---

## 📊 Data Classes

### **Use Dataclasses for DTOs**

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class PlanSpec:
    """Human-readable plan for user approval."""

    job_id: str
    user_intent: str
    summary: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "user_intent": self.user_intent,
            "summary": self.summary,
            "steps": self.steps,
            "artifacts": self.artifacts,
        }
```

---

## 🔍 Code Review Checklist

Before committing code, verify:

- [ ] Type hints on all functions
- [ ] Docstrings on classes and public methods
- [ ] Logging at appropriate levels
- [ ] Error handling for external calls
- [ ] Tests written and passing
- [ ] No hardcoded values (use config)
- [ ] No print() statements (use logging)
- [ ] Imports organized correctly
- [ ] Line length < 100 chars
- [ ] CLAUDE.md updated
- [ ] TODO.md updated

---

## 🎯 Patterns to Follow

### **From Existing Code (Planner)**

Study `skynet/core/planner.py` for examples of:
- ✅ Proper class structure
- ✅ Type hints
- ✅ Async patterns
- ✅ Logging
- ✅ Error handling
- ✅ Docstrings

### **Anti-Patterns to Avoid**

❌ Global state (use dependency injection)
❌ print() for output (use logging)
❌ Blocking I/O (use async)
❌ Broad exception catching
❌ Magic numbers (use constants)
❌ Undocumented code

---

## 📚 Additional Resources

- Python Type Hints: https://docs.python.org/3/library/typing.html
- Async/Await: https://docs.python.org/3/library/asyncio.html
- Google Docstring Style: https://google.github.io/styleguide/pyguide.html

---

**Follow these patterns for consistent, maintainable code!** ✅

### Dispatcher Mapping Pattern

When mapping natural-language plan steps to execution actions:
- Use ordered pattern checks (specific/high-risk patterns before broad ones).
- Use word-boundary regex for keyword detection (for example, match test as a word, not inside latest).
- Keep a safe fallback action for unmapped steps and record which steps used fallback.
- Make queue dispatch injectable for tests to avoid external broker dependencies.


### Ledger Reliability Pattern

For DB-backed coordination components (worker registry and job locks):
- Keep methods atomic and idempotent where possible (INSERT OR IGNORE, scoped DELETE/UPDATE).
- Use explicit UTC timestamps and timeout-based cleanup methods.
- Return simple booleans/dicts from ledger APIs to keep orchestrator and worker integration straightforward.


### Worker Reliability Runtime Pattern

For synchronous worker tasks that use async ledger helpers:
- Initialize async DB-backed components lazily and cache them per worker process.
- Acquire and release job locks in 	ry/finally blocks to avoid orphaned locks.
- Update worker heartbeat/status on pickup, completion, and health checks.
- Provide explicit shutdown hooks for tests and graceful process exits.


### Orchestrator Persistence Pattern

When adding database-backed persistence to orchestrators/services:
- Keep in-memory cache as an optimization, but persist authoritative state to DB with upsert semantics.
- Isolate row-to-model/model-to-row conversion in helper methods.
- Keep external dependencies import-light by using TYPE_CHECKING for heavy runtime-only type imports.
- Always add at least one restart/persistence test (new instance reads prior state).


### E2E Test Determinism Pattern

For end-to-end tests in mixed async/sync systems:
- Use fake planners/stubs for deterministic plan output instead of live AI calls.
- Normalize integration payloads at boundaries (support legacy and new spec shapes).
- Call synchronous worker/Celery task functions from async tests via syncio.to_thread(...).
- Keep each E2E scenario assertion-focused (READ_ONLY, WRITE, ADMIN, cancel, error, multi-step).


### Spec Compatibility Boundary Pattern

At worker boundaries, normalize incoming payload shape before execution:
- Accept both legacy (ctions) and modern dispatcher (steps) spec formats.
- Convert to one internal action representation before provider dispatch.
- Cover boundary behavior with a dedicated regression test (	est_worker_steps_format.py).

