# SKYNET/CHATHAN — Detailed Implementation Plan

**Status**: Ready for Implementation
**Target**: Complete Production Architecture
**Priority**: Sequential phases with clear dependencies

---

## Phase 1: SKYNET Core (THE BRAIN) — CRITICAL PATH

**Goal**: Build the orchestration intelligence that plans and governs all execution.

**Dependency**: None (start here)
**Estimated Effort**: 3-5 days
**Risk**: HIGH (core system logic)

### 1.1 — skynet/core/planner.py

**Purpose**: Convert user intent → human-readable PlanSpec

**Dependencies**:
- ✅ skynet/chathan/protocol/plan_spec.py (exists)
- ✅ skynet/policy/engine.py (exists)
- ✅ skynet/ledger/models.py (exists)
- AI provider (use openclaw-gateway's AI router or direct Claude API)

**Responsibilities**:
1. Accept user intent string (from Telegram /task command)
2. Call AI (Claude/GPT) to break down task into steps
3. Generate PlanSpec with:
   - job_id
   - user_intent_summary
   - proposed_steps (list of human-readable descriptions)
   - estimated_risk_level (READ_ONLY/WRITE/ADMIN)
   - expected_artifacts
4. Store plan in ledger as Job.plan_spec

**Key Functions**:
```python
class Planner:
    def __init__(self, ai_provider, policy_engine, ledger):
        ...

    async def generate_plan(self, job_id: str, user_intent: str) -> PlanSpec:
        """
        1. Build prompt with user intent + system context
        2. Call AI to decompose into steps
        3. Classify risk for each step
        4. Build PlanSpec object
        5. Return for approval
        """

    async def _build_planning_prompt(self, user_intent: str) -> str:
        """Create AI prompt for task breakdown"""

    async def _classify_step_risk(self, step: dict) -> str:
        """Classify individual step risk level"""
```

**AI Prompt Template**:
```
You are SKYNET, an autonomous orchestrator. Break down this task:

USER INTENT: {user_intent}

Generate a step-by-step plan with:
1. Clear, actionable steps
2. Risk classification for each step (READ_ONLY/WRITE/ADMIN)
3. Expected artifacts/outputs
4. Estimated execution order

Output JSON format:
{
  "summary": "...",
  "steps": [
    {"description": "...", "risk": "READ_ONLY", "tools": [...]}
  ],
  "artifacts": [...]
}
```

**Integration Points**:
- Called by: orchestrator.py
- Stores in: ledger (Job.plan_spec)
- Uses: Policy Engine for risk classification

---

### 1.2 — skynet/core/dispatcher.py

**Purpose**: Convert PlanSpec → executable ExecutionSpec + enqueue job

**Dependencies**:
- ✅ skynet/chathan/protocol/execution_spec.py (exists)
- ✅ skynet/policy/engine.py (exists)
- ✅ skynet/queue/celery_app.py (exists)
- ⚠️ skynet/ledger/job_locking.py (MISSING - build in Phase 2)

**Responsibilities**:
1. Accept approved PlanSpec
2. Convert human-readable steps → machine-executable ExecutionSpec
3. Validate ExecutionSpec against policy rules
4. Enqueue job in Celery queue
5. Update ledger status to QUEUED

**Key Functions**:
```python
class Dispatcher:
    def __init__(self, policy_engine, queue, ledger):
        ...

    async def dispatch(self, job_id: str, plan_spec: PlanSpec) -> ExecutionSpec:
        """
        1. Load Job from ledger
        2. Convert PlanSpec → ExecutionSpec
        3. Validate with policy engine
        4. If valid, enqueue job
        5. Update ledger to QUEUED
        6. Return ExecutionSpec
        """

    async def _plan_to_execution(self, plan_spec: PlanSpec) -> ExecutionSpec:
        """
        Map human steps to concrete execution actions:
        - "Check git status" → ExecutionStep(action="git_status", params={...})
        - "Run tests" → ExecutionStep(action="run_tests", params={...})
        """

    async def _validate_and_enqueue(self, exec_spec: ExecutionSpec) -> bool:
        """
        1. Policy validation
        2. Enqueue in Celery
        3. Update ledger
        """
```

**Step Mapping Logic**:
```python
# Map plan step descriptions to concrete actions
STEP_PATTERNS = {
    r"git.*status": {"action": "git_status", "params": {"working_dir": "{sandbox}"}},
    r"run.*test": {"action": "run_tests", "params": {"runner": "pytest"}},
    r"build.*project": {"action": "build_project", "params": {"build_tool": "npm"}},
    # ... more patterns
}

async def _map_step_to_action(step_description: str) -> ExecutionStep:
    """Pattern match description → action + params"""
```

**Integration Points**:
- Called by: orchestrator.py (after plan approval)
- Enqueues to: Celery queue
- Updates: ledger (Job.execution_spec, status=QUEUED)

---

### 1.3 — skynet/core/orchestrator.py

**Purpose**: Main control loop — the brain's coordinator

**Dependencies**:
- ⚠️ skynet/core/planner.py (MISSING - build first)
- ⚠️ skynet/core/dispatcher.py (MISSING - build second)
- ✅ skynet/policy/engine.py (exists)
- ✅ skynet/ledger/store.py (exists)
- ✅ skynet/queue/celery_app.py (exists)

**Responsibilities**:
1. **Receive commands** from Telegram Gateway
2. **Manage job lifecycle**: CREATED → PLANNED → QUEUED → RUNNING → FINISHED
3. **Coordinate** planner, policy, dispatcher
4. **Handle approvals** (block until user approves)
5. **Stream status** back to Telegram

**Key Functions**:
```python
class Orchestrator:
    def __init__(self, planner, dispatcher, policy_engine, ledger, queue):
        self.planner = planner
        self.dispatcher = dispatcher
        self.policy_engine = policy_engine
        self.ledger = ledger
        self.queue = queue
        self._approval_futures = {}  # job_id -> Future

    async def create_task(self, user_intent: str, project_id: str) -> str:
        """
        1. Generate job_id
        2. Create Job in ledger (status=CREATED)
        3. Return job_id for tracking
        """

    async def generate_plan(self, job_id: str) -> PlanSpec:
        """
        1. Load Job from ledger
        2. Call planner.generate_plan(user_intent)
        3. Classify risk with policy engine
        4. Update Job (plan_spec, status=PLANNED)
        5. Return PlanSpec for display
        """

    async def approve_plan(self, job_id: str) -> None:
        """
        1. Mark Job as approved
        2. Call dispatcher.dispatch(plan_spec)
        3. Job transitions to QUEUED
        4. Execution begins automatically
        """

    async def cancel_job(self, job_id: str) -> None:
        """
        1. Update Job status to CANCELLED
        2. Send cancel signal to provider (if running)
        3. Remove from queue (if queued)
        """

    async def get_status(self, job_id: str) -> dict:
        """
        Return current job status + logs + artifacts
        """

    async def wait_for_approval(self, job_id: str, timeout: int = 300) -> bool:
        """
        Block until user approves/denies (for Telegram integration)
        Returns: True if approved, False if denied/timeout
        """
```

**State Machine**:
```python
# Job Lifecycle States
CREATED → generate_plan() → PLANNED
PLANNED → approve_plan() → QUEUED (via dispatcher)
QUEUED → worker picks up → RUNNING
RUNNING → execution completes → SUCCEEDED | FAILED
Any state → cancel_job() → CANCELLED
```

**Integration Points**:
- Called by: Telegram bot handlers
- Calls: planner, dispatcher, policy engine
- Updates: ledger continuously
- Notifications: sends updates to Telegram via callback

---

### 1.4 — Integration: skynet/main.py (NEW FILE)

**Purpose**: Startup script that wires everything together

**Responsibilities**:
1. Initialize all components (ledger, queue, policy, planner, dispatcher, orchestrator)
2. Start Telegram bot
3. Start Celery worker
4. Start Sentinel monitoring
5. Health checks on startup

**Startup Sequence**:
```python
async def main():
    # 1. Initialize database
    ledger = await init_ledger()

    # 2. Initialize policy engine
    policy_engine = PolicyEngine(auto_approve_read_only=True)

    # 3. Initialize AI provider (for planner)
    ai_provider = init_ai_provider()

    # 4. Initialize queue (Celery)
    queue = init_celery_queue()

    # 5. Build core components
    planner = Planner(ai_provider, policy_engine, ledger)
    dispatcher = Dispatcher(policy_engine, queue, ledger)
    orchestrator = Orchestrator(planner, dispatcher, policy_engine, ledger, queue)

    # 6. Initialize execution engine + providers
    execution_engine = ExecutionEngine()
    execution_engine.register_provider("openclaw", OpenClawProvider())

    # 7. Start Telegram bot
    telegram_bot = build_telegram_bot(orchestrator)
    await telegram_bot.initialize()

    # 8. Start Sentinel monitoring
    sentinel = Sentinel(ledger, execution_engine)
    await sentinel.start()

    # 9. Run event loop
    await asyncio.gather(
        telegram_bot.run_polling(),
        sentinel.monitor_loop(),
        queue.run_worker(),
    )
```

---

## Phase 2: Ledger Completion (Reliability)

**Goal**: Complete distributed job management with locking and worker registry

**Dependency**: Phase 1 complete (Core needs these)
**Estimated Effort**: 1-2 days
**Risk**: MEDIUM

### 2.1 — skynet/ledger/worker_registry.py

**Purpose**: Track worker heartbeats and health

**Responsibilities**:
1. Register workers on connect
2. Track heartbeats (timestamp)
3. Mark workers OFFLINE if heartbeat expires
4. Track worker capabilities (shell, docker, etc.)

**Key Functions**:
```python
class WorkerRegistry:
    def __init__(self, db_connection):
        self.db = db_connection
        self.heartbeat_timeout = 60  # seconds

    async def register_worker(self, worker_id: str, provider: str, capabilities: list[str]) -> Worker:
        """Create or update worker record"""

    async def heartbeat(self, worker_id: str) -> None:
        """Update last_heartbeat timestamp"""

    async def get_online_workers(self) -> list[Worker]:
        """Return workers with recent heartbeat"""

    async def mark_offline(self, worker_id: str) -> None:
        """Explicitly mark worker offline"""

    async def cleanup_stale_workers(self) -> int:
        """Mark workers offline if heartbeat expired. Returns count."""
```

**Database Operations**:
- Uses existing Worker model from [skynet/ledger/models.py](skynet/ledger/models.py:120-157)
- Updates worker.last_heartbeat
- Updates worker.status (ONLINE/OFFLINE/BUSY)

**Integration Points**:
- Called by: OpenClaw worker on connect/heartbeat
- Used by: Sentinel to monitor worker health
- Used by: Dispatcher to find available workers

---

### 2.2 — skynet/ledger/job_locking.py

**Purpose**: Distributed locking to prevent duplicate execution

**Responsibilities**:
1. Acquire lock before executing job
2. Release lock on completion
3. Handle lock expiration (timeout)
4. Prevent race conditions

**Key Functions**:
```python
class JobLockManager:
    def __init__(self, db_connection):
        self.db = db_connection
        self.lock_timeout = 300  # 5 minutes default

    async def acquire_lock(self, job_id: str, worker_id: str, timeout: int = None) -> bool:
        """
        Atomic lock acquisition:
        1. Check if lock exists and is not expired
        2. If free, create lock with expiration
        3. Return True if acquired, False if locked by another worker
        """

    async def release_lock(self, job_id: str, worker_id: str) -> bool:
        """Remove lock if owned by this worker"""

    async def extend_lock(self, job_id: str, worker_id: str, additional_seconds: int) -> bool:
        """Extend lock expiration (for long-running jobs)"""

    async def cleanup_expired_locks(self) -> int:
        """Remove expired locks. Returns count."""

    async def is_locked(self, job_id: str) -> bool:
        """Check if job is currently locked"""

    async def get_lock_owner(self, job_id: str) -> str | None:
        """Return worker_id holding the lock"""
```

**Lock Flow**:
```python
# In execution worker:
async def execute_job(job_id: str, worker_id: str):
    # 1. Try to acquire lock
    if not await lock_manager.acquire_lock(job_id, worker_id):
        logger.warning(f"Job {job_id} already locked")
        return

    try:
        # 2. Execute job
        result = await execute_steps(...)

    finally:
        # 3. Always release lock
        await lock_manager.release_lock(job_id, worker_id)
```

**Database Operations**:
- Uses existing JobLock model from [skynet/ledger/models.py](skynet/ledger/models.py:159-192)
- Atomic check-and-set operations
- Expiration-based cleanup

**Integration Points**:
- Used by: CHATHAN execution engine before executing
- Used by: Dispatcher when dequeuing jobs
- Used by: Sentinel to detect stuck jobs

---

## Phase 3: Archive Completion (Observability)

**Goal**: Complete artifact and log storage for debugging and audit

**Dependency**: Phase 1 complete
**Estimated Effort**: 1 day
**Risk**: LOW

### 3.1 — skynet/archive/artifact_store.py

**Purpose**: Store and retrieve job artifacts (files, outputs, screenshots)

**Responsibilities**:
1. Save artifacts with job_id reference
2. Support S3 and local filesystem
3. Handle artifact metadata (size, type, path)
4. Provide download URLs

**Key Functions**:
```python
class ArtifactStore:
    def __init__(self, base_path: str, s3_client=None):
        self.base_path = base_path  # e.g., "artifacts/jobs/"
        self.s3_client = s3_client

    async def store_artifact(
        self,
        job_id: str,
        artifact_name: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """
        Save artifact and return path/URL
        Local: artifacts/jobs/{job_id}/{artifact_name}
        S3: s3://bucket/jobs/{job_id}/{artifact_name}
        """

    async def get_artifact(self, job_id: str, artifact_name: str) -> bytes:
        """Retrieve artifact content"""

    async def list_artifacts(self, job_id: str) -> list[dict]:
        """
        Return list of artifacts for job:
        [{"name": "...", "size": 123, "url": "..."}]
        """

    async def delete_artifacts(self, job_id: str) -> int:
        """Delete all artifacts for job. Returns count."""
```

**Storage Layout**:
```
artifacts/
  jobs/
    job_abc123/
      screenshot.png
      test_results.xml
      build_output.log
    job_def456/
      ...
```

**Integration Points**:
- Called by: CHATHAN execution engine (after step execution)
- Called by: Telegram bot (to download and send artifacts)
- Uses: S3 client (from archive/s3_client.py)

---

### 3.2 — skynet/archive/log_store.py

**Purpose**: Store and retrieve job execution logs

**Responsibilities**:
1. Stream logs during execution
2. Store complete logs after job finishes
3. Support log queries (tail, search)
4. Rotation and cleanup

**Key Functions**:
```python
class LogStore:
    def __init__(self, base_path: str):
        self.base_path = base_path  # e.g., "logs/jobs/"

    async def append_log(self, job_id: str, log_line: str) -> None:
        """Append line to job log (streaming during execution)"""

    async def get_logs(self, job_id: str, tail: int = None) -> str:
        """
        Retrieve logs for job
        tail: if specified, return last N lines
        """

    async def store_final_logs(self, job_id: str, logs: str) -> str:
        """Store complete logs after execution. Returns path."""

    async def search_logs(self, job_id: str, pattern: str) -> list[str]:
        """Search logs for pattern, return matching lines"""

    async def cleanup_old_logs(self, days: int = 30) -> int:
        """Delete logs older than N days. Returns count."""
```

**Storage Layout**:
```
logs/
  jobs/
    job_abc123.log
    job_def456.log
```

**Integration Points**:
- Called by: CHATHAN execution engine (stream logs)
- Called by: Telegram bot (display logs)
- Called by: Sentinel (analyze errors)

---

## Phase 4: Sentinel Completion (Monitoring)

**Goal**: Complete health monitoring and alerting

**Dependency**: Phase 2 complete (needs worker registry)
**Estimated Effort**: 1 day
**Risk**: LOW

### 4.1 — skynet/sentinel/provider_monitor.py

**Purpose**: Monitor provider health and availability

**Responsibilities**:
1. Health check all registered providers
2. Detect provider failures
3. Alert on repeated failures
4. Track provider metrics (latency, success rate)

**Key Functions**:
```python
class ProviderMonitor:
    def __init__(self, execution_engine, alert_manager):
        self.execution_engine = execution_engine
        self.alert_manager = alert_manager
        self.health_history = {}  # provider -> [bool, bool, ...]

    async def check_all_providers(self) -> dict[str, bool]:
        """
        Check health of all providers
        Returns: {provider_name: is_healthy}
        """

    async def check_provider(self, provider_name: str) -> bool:
        """Health check single provider"""

    async def detect_degradation(self, provider_name: str) -> bool:
        """
        Check if provider is degraded (3+ failures in last 10 checks)
        """

    async def alert_on_failure(self, provider_name: str) -> None:
        """Send alert if provider is down"""

    def get_provider_stats(self, provider_name: str) -> dict:
        """
        Return stats:
        {
          "success_rate": 0.95,
          "avg_latency_ms": 150,
          "last_success": "2025-02-15T10:30:00Z"
        }
        """
```

**Alert Triggers**:
- Provider down (health_check fails)
- Provider degraded (success rate < 80%)
- No workers online for provider
- Provider latency spike

**Integration Points**:
- Called by: Sentinel monitor loop
- Uses: Execution engine (provider registry)
- Sends alerts via: Telegram, email, Slack

---

## Phase 5: Provider Architecture Cleanup

**Goal**: Align provider implementation with spec

**Dependency**: Phase 1-3 complete
**Estimated Effort**: 2-3 hours
**Risk**: LOW

### 5.1 — Rename/Create openclaw_provider.py

**Decision Required**:
The spec says **OpenClaw Provider** but current implementation has **chathan_provider.py**.

**Option A**: Rename chathan_provider.py → openclaw_provider.py
```python
# skynet/chathan/providers/openclaw_provider.py

class OpenClawProvider(BaseExecutionProvider):
    """
    Execute via OpenClaw Gateway → OpenClaw Worker (laptop agent)

    Architecture:
    SKYNET → OpenClaw Provider → OpenClaw Gateway (EC2) → OpenClaw Worker (Laptop)
    """
    name = "openclaw"

    def __init__(self, gateway_url: str = "http://EC2_IP:8766"):
        self.gateway_url = gateway_url

    async def execute(self, spec: ExecutionSpec) -> ExecutionResult:
        """Send ExecutionSpec to OpenClaw Gateway via HTTP"""

    async def health_check(self) -> bool:
        """Check if OpenClaw Gateway is reachable and has workers"""

    async def cancel(self, job_id: str) -> bool:
        """Send cancel signal to OpenClaw Gateway"""
```

**Option B**: Keep both providers
- `chathan_provider.py` - Direct WebSocket to worker
- `openclaw_provider.py` - HTTP to OpenClaw Gateway

**Recommendation**: **Option A** - Rename to match spec for clarity

---

### 5.2 — Update Provider Registration

**In skynet/chathan/execution/engine.py**:
```python
# Register OpenClaw as the active provider
execution_engine.register_provider("openclaw", OpenClawProvider(
    gateway_url=settings.OPENCLAW_GATEWAY_URL
))

# Future providers:
# execution_engine.register_provider("docker", DockerProvider())
# execution_engine.register_provider("ssh", SSHProvider())
```

---

## Phase 6: System Integration & Wiring

**Goal**: Connect all components into a working end-to-end system

**Dependency**: Phases 1-5 complete
**Estimated Effort**: 2-3 days
**Risk**: HIGH (integration bugs)

### 6.1 — Telegram Bot → SKYNET Core Integration

**Modify skynet/gateway/telegram_bot.py**:

Currently it references `_project_manager` (from openclaw-gateway).
**Change to** reference `_orchestrator` (from SKYNET Core).

```python
# Old (v2 project-based):
await _project_manager.create_project(name)
await _project_manager.generate_plan(project_id)
await _project_manager.start_execution(project_id)

# New (SKYNET orchestrator-based):
job_id = await _orchestrator.create_task(user_intent, project_id)
plan = await _orchestrator.generate_plan(job_id)
await _orchestrator.approve_plan(job_id)
```

**Updated Commands**:
```python
async def cmd_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /task <description>

    1. Create job via orchestrator
    2. Generate plan
    3. Display plan with Approve/Deny buttons
    """
    if not context.args:
        await update.message.reply_text("Usage: /task <description>")
        return

    user_intent = " ".join(context.args)

    # Create job
    job_id = await orchestrator.create_task(user_intent, project_id="default")

    # Generate plan
    plan = await orchestrator.generate_plan(job_id)

    # Display plan
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Approve", callback_data=f"approve_job:{job_id}"),
        InlineKeyboardButton("Cancel", callback_data=f"cancel_job:{job_id}"),
    ]])

    await update.message.reply_text(
        plan.to_markdown(),
        parse_mode="Markdown",
        reply_markup=keyboard,
    )

async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /approve <job_id>

    Approve plan and start execution
    """
    if not context.args:
        await update.message.reply_text("Usage: /approve <job_id>")
        return

    job_id = context.args[0]

    await orchestrator.approve_plan(job_id)
    await update.message.reply_text(f"✅ Job {job_id} approved. Execution started.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /status <job_id>

    Show job status
    """
    if not context.args:
        await update.message.reply_text("Usage: /status <job_id>")
        return

    job_id = context.args[0]
    status = await orchestrator.get_status(job_id)

    await update.message.reply_text(
        f"**Job {job_id}**\n"
        f"Status: {status['status']}\n"
        f"Progress: {status['progress']}\n"
        f"Logs: {status['logs'][:500]}...",
        parse_mode="Markdown",
    )

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /cancel <job_id>

    Cancel running job
    """
    if not context.args:
        await update.message.reply_text("Usage: /cancel <job_id>")
        return

    job_id = context.args[0]
    await orchestrator.cancel_job(job_id)
    await update.message.reply_text(f"🛑 Job {job_id} cancelled.")
```

---

### 6.2 — Celery Queue Integration

**Create skynet/queue/worker.py**:

```python
"""
Celery worker that executes jobs from the queue.

This worker:
1. Picks up jobs from Redis queue
2. Acquires job lock
3. Calls CHATHAN execution engine
4. Updates ledger with results
"""

from celery import Celery
from skynet.queue.celery_app import app
from skynet.chathan.execution.engine import ExecutionEngine
from skynet.ledger import store
from skynet.ledger.job_locking import JobLockManager
import logging

logger = logging.getLogger("skynet.worker")

execution_engine = None  # Initialized on worker startup
lock_manager = None
ledger = None

@app.task(name="skynet.execute_job")
async def execute_job_task(job_id: str, worker_id: str):
    """
    Main execution task:
    1. Acquire lock
    2. Load ExecutionSpec from ledger
    3. Execute via CHATHAN engine
    4. Store results
    5. Release lock
    """

    # 1. Try to acquire lock
    if not await lock_manager.acquire_lock(job_id, worker_id):
        logger.warning(f"Job {job_id} already locked by another worker")
        return {"status": "skipped", "reason": "already_locked"}

    try:
        # 2. Load job
        job = await ledger.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        exec_spec = ExecutionSpec.from_dict(job.execution_spec)

        # 3. Update status to RUNNING
        await ledger.update_job_status(job_id, "RUNNING", worker_id=worker_id)

        # 4. Execute via CHATHAN
        result = await execution_engine.execute(exec_spec)

        # 5. Store results
        if result.succeeded:
            await ledger.update_job_status(
                job_id,
                "SUCCEEDED",
                result_summary=result.logs[:500],
            )
        else:
            await ledger.update_job_status(
                job_id,
                "FAILED",
                error_message=result.error,
            )

        # 6. Store logs and artifacts
        await log_store.store_final_logs(job_id, result.logs)
        for artifact in result.artifacts:
            await artifact_store.store_artifact(job_id, artifact)

        return result.to_dict()

    except Exception as e:
        logger.error(f"Job {job_id} execution failed: {e}")
        await ledger.update_job_status(job_id, "FAILED", error_message=str(e))
        return {"status": "error", "error": str(e)}

    finally:
        # 7. Always release lock
        await lock_manager.release_lock(job_id, worker_id)
```

**Start Worker**:
```bash
# In terminal
celery -A skynet.queue.celery_app worker --loglevel=info
```

---

### 6.3 — CHATHAN Execution Engine → Provider Router

**Ensure skynet/chathan/execution/engine.py properly routes to providers**:

```python
class ExecutionEngine:
    def __init__(self):
        self.providers = {}  # provider_name -> BaseExecutionProvider

    def register_provider(self, name: str, provider: BaseExecutionProvider):
        """Register an execution provider"""
        self.providers[name] = provider
        logger.info(f"Registered provider: {name}")

    async def execute(self, spec: ExecutionSpec) -> ExecutionResult:
        """
        Route execution to the appropriate provider.

        1. Look up provider by name (spec.provider)
        2. Validate spec
        3. Call provider.execute(spec)
        4. Return result
        """
        provider_name = spec.provider or "openclaw"

        provider = self.providers.get(provider_name)
        if not provider:
            raise ValueError(f"Provider '{provider_name}' not registered")

        # Health check before execution
        if not await provider.health_check():
            raise RuntimeError(f"Provider '{provider_name}' is unhealthy")

        # Execute
        logger.info(f"Executing job {spec.job_id} via provider {provider_name}")
        result = await provider.execute(spec)

        return result

    async def cancel(self, job_id: str, provider_name: str) -> bool:
        """Cancel job via provider"""
        provider = self.providers.get(provider_name)
        if not provider:
            return False
        return await provider.cancel(job_id)

    async def health_check_all(self) -> dict[str, bool]:
        """Check health of all providers"""
        return {
            name: await provider.health_check()
            for name, provider in self.providers.items()
        }
```

---

### 6.4 — OpenClaw Gateway API Integration

**Ensure OpenClaw Gateway exposes the right endpoints**:

The OpenClaw Gateway (EC2) must accept ExecutionSpec from SKYNET.

**Required Endpoint in openclaw-gateway/api.py**:

```python
@app.post("/execute")
async def execute_job(request: Request):
    """
    Accept ExecutionSpec from SKYNET and dispatch to worker.

    Request body:
    {
      "job_id": "...",
      "execution_spec": {...}
    }

    Returns:
    {
      "status": "accepted",
      "job_id": "..."
    }
    """
    data = await request.json()
    exec_spec = ExecutionSpec.from_dict(data["execution_spec"])

    # Send to connected worker via WebSocket
    if not agent_connected:
        return {"status": "error", "error": "No worker connected"}

    # Queue job for worker
    job_queue.put(exec_spec)

    return {"status": "accepted", "job_id": exec_spec.job_id}

@app.get("/status/{job_id}")
async def get_job_status(job_id: str):
    """
    Get job execution status.

    Returns:
    {
      "status": "running" | "succeeded" | "failed",
      "logs": "...",
      "exit_code": 0
    }
    """
    # Look up job in worker queue or completed jobs
    ...

@app.post("/cancel/{job_id}")
async def cancel_job(job_id: str):
    """Cancel running job"""
    # Send cancel signal to worker
    ...
```

---

### 6.5 — Environment Configuration

**Create skynet/.env.example**:

Already exists at [skynet/.env.example](skynet/.env.example).

**Add missing variables**:
```bash
# SKYNET Core
SKYNET_PROJECT_ID=default
SKYNET_LOG_LEVEL=INFO

# Ledger (SQLite or Postgres)
DATABASE_URL=sqlite:///skynet.db

# Redis + Celery
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# OpenClaw Gateway (EC2)
OPENCLAW_GATEWAY_URL=http://YOUR_EC2_IP:8766

# AI Provider (for Planner)
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-...

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_ID=...

# Archive
ARTIFACT_STORE_PATH=./artifacts
LOG_STORE_PATH=./logs
S3_BUCKET=skynet-artifacts  # optional

# Sentinel
HEARTBEAT_TIMEOUT_SECONDS=60
JOB_TIMEOUT_SECONDS=300
```

---

## Phase 7: End-to-End Testing

**Goal**: Validate the complete system flow

**Dependency**: Phase 6 complete
**Estimated Effort**: 2-3 days
**Risk**: HIGH (integration issues)

### 7.1 — Test Flow: Simple READ_ONLY Task

**Test**: `/task check git status in my project`

**Expected Flow**:
1. ✅ Telegram receives command
2. ✅ Orchestrator creates job (CREATED)
3. ✅ Planner generates PlanSpec
4. ✅ Policy classifies as READ_ONLY
5. ✅ Telegram displays plan (auto-approve for READ_ONLY)
6. ✅ Dispatcher creates ExecutionSpec
7. ✅ Job enqueued in Celery
8. ✅ Worker picks up job
9. ✅ Execution Engine → OpenClaw Provider → OpenClaw Gateway → Worker
10. ✅ Worker executes `git_status`
11. ✅ Results returned to SKYNET
12. ✅ Ledger updated (SUCCEEDED)
13. ✅ Telegram shows results

---

### 7.2 — Test Flow: WRITE Task (Approval Required)

**Test**: `/task create a new file test.txt with hello world`

**Expected Flow**:
1. ✅ Telegram receives command
2. ✅ Orchestrator creates job
3. ✅ Planner generates PlanSpec
4. ✅ Policy classifies as WRITE
5. ✅ Telegram displays plan with Approve/Deny buttons
6. ⏸️ **WAIT for user approval**
7. ✅ User clicks Approve
8. ✅ Dispatcher creates ExecutionSpec
9. ✅ Execution continues...
10. ✅ File created
11. ✅ Telegram shows success

---

### 7.3 — Test Flow: Cancellation

**Test**: Start long task, then cancel

1. ✅ `/task run all tests`
2. ✅ Approve plan
3. ✅ Execution starts
4. ✅ `/cancel <job_id>`
5. ✅ Job status → CANCELLED
6. ✅ Provider receives cancel signal
7. ✅ Worker stops execution
8. ✅ Telegram confirms cancellation

---

### 7.4 — Test Flow: Error Handling

**Test**: Task that fails

1. ✅ `/task run invalid command xyz123`
2. ✅ Execution starts
3. ❌ Step fails (command not found)
4. ✅ Execution stops
5. ✅ Status → FAILED
6. ✅ Error message stored
7. ✅ Telegram shows error

---

### 7.5 — Test: Multi-Step Task

**Test**: `/task check git status, run tests, and build project`

1. ✅ Planner creates 3-step plan
2. ✅ ExecutionSpec has 3 ExecutionSteps
3. ✅ Step 1: git_status → succeeds
4. ✅ Step 2: run_tests → succeeds
5. ✅ Step 3: build_project → succeeds
6. ✅ All logs captured
7. ✅ Artifacts stored
8. ✅ Telegram shows complete summary

---

## Phase 8: Production Hardening

**Goal**: Make system production-ready

**Dependency**: Phase 7 complete
**Estimated Effort**: 3-5 days
**Risk**: MEDIUM

### 8.1 — Error Recovery

**Implement**:
- Job retry logic (3 retries with exponential backoff)
- Dead letter queue for failed jobs
- Graceful degradation (if provider down, queue jobs)

### 8.2 — Performance Optimization

- Database indexes on job queries
- Redis caching for frequently accessed data
- Pagination for job lists
- Streaming logs (don't load entire log file)

### 8.3 — Security Hardening

- Input validation on all Telegram commands
- Sanitize ExecutionSpec parameters
- Rate limiting per user
- Audit log all ADMIN actions

### 8.4 — Observability

- Prometheus metrics export
- Grafana dashboards
- Structured logging (JSON)
- OpenTelemetry tracing

### 8.5 — Deployment

- Docker Compose for full stack
- Systemd services for production
- Auto-restart on failure
- Health check endpoints

---

## Summary: Implementation Sequence

| Phase | Component | Priority | Est. Days | Dependencies |
|-------|-----------|----------|-----------|--------------|
| **1** | **SKYNET Core** | 🔴 CRITICAL | 3-5 | None |
| 1.1 | planner.py | 🔴 | 1-2 | AI provider |
| 1.2 | dispatcher.py | 🔴 | 1 | planner.py |
| 1.3 | orchestrator.py | 🔴 | 1-2 | planner, dispatcher |
| 1.4 | main.py | 🔴 | 0.5 | All core |
| **2** | **Ledger Completion** | 🟡 HIGH | 1-2 | Phase 1 |
| 2.1 | worker_registry.py | 🟡 | 0.5 | - |
| 2.2 | job_locking.py | 🟡 | 1 | - |
| **3** | **Archive Completion** | 🟢 MEDIUM | 1 | Phase 1 |
| 3.1 | artifact_store.py | 🟢 | 0.5 | - |
| 3.2 | log_store.py | 🟢 | 0.5 | - |
| **4** | **Sentinel Completion** | 🟢 MEDIUM | 1 | Phase 2 |
| 4.1 | provider_monitor.py | 🟢 | 1 | worker_registry |
| **5** | **Provider Cleanup** | 🟢 LOW | 0.5 | Phase 1-3 |
| 5.1 | Rename to openclaw_provider | 🟢 | 0.1 | - |
| 5.2 | Update registration | 🟢 | 0.1 | - |
| **6** | **System Integration** | 🔴 CRITICAL | 2-3 | Phase 1-5 |
| 6.1 | Telegram bot integration | 🔴 | 1 | orchestrator |
| 6.2 | Celery worker | 🔴 | 0.5 | execution_engine |
| 6.3 | Execution engine routing | 🔴 | 0.5 | providers |
| 6.4 | Gateway API integration | 🔴 | 1 | OpenClaw Gateway |
| 6.5 | Environment config | 🟢 | 0.2 | - |
| **7** | **Testing** | 🔴 CRITICAL | 2-3 | Phase 6 |
| 7.1-7.5 | E2E test scenarios | 🔴 | 2-3 | Full system |
| **8** | **Production Hardening** | 🟡 HIGH | 3-5 | Phase 7 |
| 8.1-8.5 | Error recovery, perf, security | 🟡 | 3-5 | Tested system |

**Total Estimated Effort**: 15-22 days

---

## Next Steps

1. **Review this plan** — Confirm approach and priorities
2. **Start with Phase 1** — Build SKYNET Core (the brain)
3. **Incremental testing** — Test each component as built
4. **Iterate** — Adjust based on learnings

Ready to begin implementation? Let me know which phase to start with!
