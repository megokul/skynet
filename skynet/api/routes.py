"""
SKYNET API Routes - FastAPI endpoint handlers.

Implements the SKYNET control plane API:
- POST /v1/plan - Generate execution plan
- POST /v1/report - Receive progress updates
- POST /v1/policy/check - Policy validation
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends, Header, Request

from skynet.api import schemas
from skynet.api.schemas import (
    PlanRequest,
    PlanResponse,
    ReportRequest,
    ReportResponse,
    PolicyCheckRequest,
    PolicyCheckResponse,
    HealthResponse,
    ExecutionDecision,
    ExecutionMode,
    RiskLevel,
    ModelPolicy,
    ProviderType,
    ExecutionStep,
    ApprovalGate,
    ArtifactConfig,
    WorkerTarget,
)
from skynet.policy.engine import PolicyEngine

if TYPE_CHECKING:
    from skynet.core.planner import Planner

logger = logging.getLogger("skynet.api")

router = APIRouter(prefix="/v1", tags=["skynet"])


# ============================================================================
# Dependencies
# ============================================================================


class AppState:
    """Application state container."""

    planner: Planner | None = None
    policy_engine: PolicyEngine | None = None
    memory_manager: Any | None = None  # MemoryManager from skynet.memory
    vector_indexer: Any | None = None  # VectorIndexer from skynet.memory
    event_engine: Any | None = None  # EventEngine from skynet.events
    provider_monitor: Any | None = None
    scheduler: Any | None = None
    execution_router: Any | None = None
    ledger_db: Any | None = None
    worker_registry: Any | None = None
    report_store: dict[UUID, list[dict]] = {}  # Simple in-memory store


app_state = AppState()
_rate_limit_buckets: dict[str, tuple[float, int]] = {}


def get_planner():
    """Dependency: Get Planner instance."""
    if app_state.planner is None:
        raise HTTPException(status_code=503, detail="Planner not initialized")
    return app_state.planner


def get_policy_engine() -> PolicyEngine:
    """Dependency: Get PolicyEngine instance."""
    if app_state.policy_engine is None:
        raise HTTPException(status_code=503, detail="PolicyEngine not initialized")
    return app_state.policy_engine


def get_execution_router():
    """Dependency: Get shared ExecutionRouter instance."""
    if app_state.execution_router is None:
        raise HTTPException(status_code=503, detail="Execution router not initialized")
    return app_state.execution_router


def get_scheduler():
    """Dependency: Get shared ProviderScheduler instance."""
    if app_state.scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler not initialized")
    return app_state.scheduler


def get_provider_monitor():
    """Dependency: Get shared ProviderMonitor instance."""
    if app_state.provider_monitor is None:
        raise HTTPException(status_code=503, detail="Provider monitor not initialized")
    return app_state.provider_monitor


def _is_auth_required() -> bool:
    return os.getenv("SKYNET_PROTECT_DIAGNOSTICS", "true").lower() == "true"


def _resolve_api_key() -> str:
    return os.getenv("SKYNET_API_KEY", "").strip()


def _extract_token(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()

    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()

    return None


def _enforce_rate_limit(request: Request) -> None:
    limit = int(os.getenv("SKYNET_DIAGNOSTIC_RATE_LIMIT_PER_MIN", "120"))
    if limit <= 0:
        return

    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    window_start, count = _rate_limit_buckets.get(client_ip, (now, 0))

    if now - window_start >= 60:
        window_start, count = now, 0

    count += 1
    _rate_limit_buckets[client_ip] = (window_start, count)

    if count > limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


def require_protected_route_access(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> bool:
    """
    Guard for control/diagnostic endpoints.

    - Applies lightweight in-memory rate limiting.
    - Enforces API key auth when SKYNET_API_KEY is configured.
    """
    _enforce_rate_limit(request)

    if not _is_auth_required():
        return False

    configured_key = _resolve_api_key()
    if not configured_key:
        return False

    token = _extract_token(authorization, x_api_key)
    if token != configured_key:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return True


def _redact_provider_dashboard(data: dict[str, Any]) -> dict[str, Any]:
    """
    Remove potentially sensitive provider error details from public responses.
    """
    redacted = dict(data)
    providers = redacted.get("providers", {})
    sanitized_providers: dict[str, dict[str, Any]] = {}
    for name, details in providers.items():
        details_copy = dict(details)
        if details_copy.get("status") == "unhealthy":
            details_copy["message"] = "Provider unhealthy (details redacted)"
            details_copy["details"] = {}
        sanitized_providers[name] = details_copy
    redacted["providers"] = sanitized_providers

    history = redacted.get("history", [])
    sanitized_history = []
    for snapshot in history:
        snapshot_copy = dict(snapshot)
        snap_providers = snapshot_copy.get("providers")
        if isinstance(snap_providers, dict):
            clean_snap_providers: dict[str, dict[str, Any]] = {}
            for provider_name, provider_details in snap_providers.items():
                pd = dict(provider_details)
                if pd.get("status") == "unhealthy":
                    pd["message"] = "Provider unhealthy (details redacted)"
                    pd["details"] = {}
                clean_snap_providers[provider_name] = pd
            snapshot_copy["providers"] = clean_snap_providers
        sanitized_history.append(snapshot_copy)
    redacted["history"] = sanitized_history
    return redacted


# ============================================================================
# POST /v1/plan - Generate Execution Plan
# ============================================================================


@router.post("/plan", response_model=PlanResponse)
async def create_plan(
    request: PlanRequest,
    planner=Depends(get_planner),
    policy: PolicyEngine = Depends(get_policy_engine),
) -> PlanResponse:
    """
    Generate an execution plan from user intent.

    This endpoint:
    1. Uses AI (Gemini) to decompose the task into steps
    2. Classifies risk level (LOW/MEDIUM/HIGH)
    3. Determines approval gates
    4. Returns structured execution plan
    """
    logger.info(f"Plan request {request.request_id}: {request.user_message[:50]}...")

    try:
        # Build context for planner
        planner_context = {
            "repo": request.context.repo,
            "branch": request.context.branch,
            "environment": request.context.environment.value,
            "recent_actions": request.context.recent_actions,
            "constraints": {
                "max_cost_usd": request.constraints.max_cost_usd,
                "time_budget_min": request.constraints.time_budget_min,
                "allowed_targets": [t.value for t in request.constraints.allowed_targets],
            },
        }

        # Generate plan using AI
        plan_data = await planner.generate_plan(
            job_id=str(request.request_id),
            user_intent=request.user_message,
            context=planner_context,
        )

        # For MVP: Skip PlanSpec creation due to structure mismatch
        # Just get risk level directly from planner output
        max_risk_level = plan_data.get("max_risk_level", "WRITE")

        # Simple policy validation based on risk level
        requires_approval = max_risk_level in ("WRITE", "ADMIN")

        # Map risk level
        risk_level_map = {
            "READ_ONLY": RiskLevel.LOW,
            "WRITE": RiskLevel.MEDIUM,
            "ADMIN": RiskLevel.HIGH,
        }
        risk_level = risk_level_map.get(max_risk_level, RiskLevel.MEDIUM)

        # Determine execution mode
        execution_mode = ExecutionMode.EXECUTE
        if requires_approval:
            reason = "Requires approval before execution"
        else:
            reason = "Auto-approved (low risk)"

        # Build execution steps
        execution_steps = []
        for idx, step in enumerate(plan_data.get("steps", []), start=1):
            # Determine target based on step type
            target = _determine_target(step, request.constraints.allowed_targets)

            # Determine agent type based on step description
            agent = _determine_agent(step)

            execution_steps.append(
                ExecutionStep(
                    step=idx,
                    agent=agent,
                    action=step.get("command", step.get("description", "unknown")),
                    target=target,
                    description=step.get("description"),
                    estimated_time_min=step.get("estimated_time_minutes"),
                )
            )

        # Build approval gates
        approval_gates = []
        for idx, step in enumerate(plan_data.get("steps", []), start=1):
            step_desc = step.get("description", "").lower()
            step_command = step.get("command", "").lower()

            # Check if step matches approval requirements
            for approval_action in request.constraints.requires_approval_for:
                if approval_action.lower() in step_desc or approval_action.lower() in step_command:
                    approval_gates.append(
                        ApprovalGate(
                            gate=approval_action,
                            required=True,
                            when_step=idx,
                            reason=f"Step involves {approval_action}",
                        )
                    )

        # Build model policy (cost optimization)
        model_policy = ModelPolicy(
            default=ProviderType.LOCAL,
            escalation=[ProviderType.FREE_API, ProviderType.PAID_API],
        )

        # Build decision
        decision = ExecutionDecision(
            mode=execution_mode,
            risk_level=risk_level,
            model_policy=model_policy,
            reason=reason,
        )

        # Build artifact config
        artifacts = ArtifactConfig(
            s3_prefix=f"s3://skynet-artifacts/runs/{request.request_id}/",
            retention_days=30,
        )

        # Build response
        response = PlanResponse(
            request_id=request.request_id,
            decision=decision,
            execution_plan=execution_steps,
            approval_gates=approval_gates,
            artifacts=artifacts,
        )

        logger.info(
            f"Plan generated: {len(execution_steps)} steps, "
            f"risk={risk_level.value}, mode={execution_mode.value}"
        )

        return response

    except Exception as e:
        logger.error(f"Plan generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Plan generation failed: {str(e)}")


# ============================================================================
# POST /v1/report - Receive Progress Updates
# ============================================================================


@router.post("/report", response_model=ReportResponse)
async def receive_report(request: ReportRequest) -> ReportResponse:
    """
    Receive progress report from OpenClaw.

    Stores execution progress and provides feedback.
    """
    logger.info(
        f"Report received for {request.request_id}: "
        f"{len(request.step_reports)} steps, status={request.overall_status.value}"
    )

    # Store report (simple in-memory for now, will use DB later)
    if request.request_id not in app_state.report_store:
        app_state.report_store[request.request_id] = []

    app_state.report_store[request.request_id].append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "step_reports": [report.dict() for report in request.step_reports],
            "overall_status": request.overall_status.value,
            "metadata": request.metadata,
        }
    )

    # Determine next action based on status
    next_action = None
    if request.overall_status.value == "failed":
        next_action = "Review error logs and retry failed steps"
    elif request.overall_status.value == "completed":
        next_action = "Task completed successfully"

    return ReportResponse(
        request_id=request.request_id,
        received=True,
        next_action=next_action,
    )


# ============================================================================
# POST /v1/policy/check - Policy Validation
# ============================================================================


@router.post("/policy/check", response_model=PolicyCheckResponse)
async def check_policy(
    request: PolicyCheckRequest,
    policy: PolicyEngine = Depends(get_policy_engine),
) -> PolicyCheckResponse:
    """
    Check if an action is allowed by policy.

    Used by OpenClaw to validate individual actions before execution.
    """
    logger.info(f"Policy check: action={request.action}, target={request.target}")

    # Import here to avoid circular dependencies
    from skynet.policy.rules import classify_action_risk, BLOCKED_ACTIONS

    # Check if action is blocked
    action_lower = request.action.lower()
    if any(blocked in action_lower for blocked in BLOCKED_ACTIONS):
        return PolicyCheckResponse(
            allowed=False,
            reason=f"Action '{request.action}' is blocked by policy",
            requires_approval=False,
            risk_level=RiskLevel.HIGH,
        )

    # Classify risk
    risk_level_str = classify_action_risk(request.action)
    risk_level_map = {
        "READ_ONLY": RiskLevel.LOW,
        "WRITE": RiskLevel.MEDIUM,
        "ADMIN": RiskLevel.HIGH,
    }
    risk_level = risk_level_map.get(risk_level_str, RiskLevel.MEDIUM)

    # Determine if approval required
    requires_approval = policy.requires_approval(risk_level_str)

    return PolicyCheckResponse(
        allowed=True,
        reason=f"Action classified as {risk_level_str}",
        requires_approval=requires_approval,
        risk_level=risk_level,
    )


# ============================================================================
# POST /execute - Direct Synchronous Execution (SKYNET 2.0 - Phase 4)
# ============================================================================


@router.post("/execute", response_model=schemas.ExecuteResponse)
async def execute_direct(
    request: schemas.ExecuteRequest,
    execution_router=Depends(get_execution_router),
    _authorized=Depends(require_protected_route_access),
):
    """
    Execute task directly without queue (synchronous).

    Bypasses Celery queue for immediate execution. Useful for:
    - Interactive commands
    - Health checks
    - Quick queries
    - Testing

    Maximum timeout: 30 minutes (1800 seconds)
    """
    logger.info(f"Direct execution request received (timeout={request.timeout})")

    # Execute directly
    try:
        result = await execution_router.execute_plan(
            execution_spec=request.execution_spec,
            total_timeout=request.timeout,
        )

        # Convert to response schema
        step_results = [
            schemas.ExecuteStepResult(
                action=step.get("action", ""),
                status=step.get("status", "unknown"),
                output=step.get("output", ""),
                stdout=step.get("stdout", ""),
                stderr=step.get("stderr", ""),
                error=step.get("error"),
            )
            for step in result.get("results", [])
        ]

        return schemas.ExecuteResponse(
            job_id=result.get("job_id", "unknown"),
            status=result.get("status", "unknown"),
            provider=result.get("provider", "unknown"),
            results=step_results,
            steps_completed=result.get("steps_completed", 0),
            steps_total=result.get("steps_total", 0),
            elapsed_seconds=result.get("elapsed_seconds", 0.0),
            error=result.get("error"),
            timeout_seconds=result.get("timeout_seconds"),
        )

    except Exception as e:
        logger.exception("Direct execution failed")
        raise HTTPException(
            status_code=500,
            detail=f"Execution failed: {str(e)}",
        )


@router.post("/scheduler/diagnose", response_model=schemas.SchedulerDiagnoseResponse)
async def diagnose_scheduler(
    request: schemas.SchedulerDiagnoseRequest,
    scheduler=Depends(get_scheduler),
    _authorized=Depends(require_protected_route_access),
):
    """
    Diagnose scheduler provider selection for a given execution spec.

    Returns candidate providers, score breakdown, and final selection.
    """
    try:
        result = await scheduler.diagnose_selection(
            execution_spec=request.execution_spec,
            fallback=request.fallback,
        )
        return schemas.SchedulerDiagnoseResponse(**result)
    except Exception as e:
        logger.exception("Scheduler diagnostics failed")
        raise HTTPException(status_code=500, detail=f"Scheduler diagnostics failed: {e}")


@router.get("/providers/health", response_model=schemas.ProviderHealthDashboardResponse)
async def provider_health_dashboard(
    provider_monitor=Depends(get_provider_monitor),
    authorized=Depends(require_protected_route_access),
):
    """
    Get provider health dashboard data from ProviderMonitor.
    """
    try:
        data = provider_monitor.get_dashboard_data()
        if not authorized and os.getenv("SKYNET_REDACT_PROVIDER_ERRORS", "true").lower() == "true":
            data = _redact_provider_dashboard(data)
        return schemas.ProviderHealthDashboardResponse(**data)
    except Exception as e:
        logger.exception("Provider health dashboard retrieval failed")
        raise HTTPException(status_code=500, detail=f"Provider health dashboard failed: {e}")


# ============================================================================
# GET /health - Health Check
# ============================================================================


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Service health check."""
    components = {
        "planner": "ok" if app_state.planner else "not_initialized",
        "policy_engine": "ok" if app_state.policy_engine else "not_initialized",
    }

    status = "ok" if all(v == "ok" for v in components.values()) else "degraded"

    return HealthResponse(
        status=status,
        version="1.0.0",
        components=components,
    )


# ============================================================================
# Memory API (SKYNET 2.0)
# ============================================================================


@router.post("/memory/store", response_model=schemas.StoreMemoryResponse)
async def store_memory(request: schemas.StoreMemoryRequest):
    """
    Manually store a memory.

    Allows external systems to add memories to SKYNET's knowledge base.
    """
    from datetime import datetime

    from skynet.memory.models import MemoryRecord, MemoryType

    if not app_state.memory_manager:
        raise HTTPException(
            status_code=503, detail="Memory system not available"
        )

    # Create memory record
    memory = MemoryRecord(
        memory_type=MemoryType(request.memory_type.value),
        content=request.content,
        metadata=request.metadata,
    )

    # Store in memory system
    memory_id = await app_state.memory_manager.storage.store_memory(memory)

    logger.info(f"Stored memory: {memory_id} (type={request.memory_type.value})")

    return schemas.StoreMemoryResponse(
        memory_id=memory_id,
        stored_at=datetime.utcnow().isoformat(),
    )


@router.post("/memory/search", response_model=schemas.SearchMemoryResponse)
async def search_memory(request: schemas.SearchMemoryRequest):
    """
    Search memories by filters.

    Returns memories matching query and filters, sorted by importance.
    """
    from skynet.memory.models import MemoryType

    if not app_state.memory_manager:
        raise HTTPException(
            status_code=503, detail="Memory system not available"
        )

    memory_type = MemoryType(request.memory_type.value) if request.memory_type else None

    # Search memories
    memories = await app_state.memory_manager.storage.search_memories(
        memory_type=memory_type,
        limit=request.limit,
    )

    # Convert to response format
    results = []
    for mem in memories:
        result = schemas.MemoryRecordResponse(
            id=mem.id,
            timestamp=mem.timestamp.isoformat(),
            memory_type=mem.memory_type.value,
            content=mem.content,
            metadata=mem.metadata,
            retrieval_count=mem.retrieval_count,
            importance_score=mem.importance_score,
            embedding=mem.embedding if request.include_embeddings else None,
        )
        results.append(result)

    logger.info(f"Memory search: found {len(results)} memories")

    return schemas.SearchMemoryResponse(
        results=results,
        count=len(results),
        query=request.query,
    )


@router.post("/memory/similar", response_model=schemas.SimilarMemoryResponse)
async def search_similar(request: schemas.SimilarMemoryRequest):
    """
    Find semantically similar memories.

    Uses vector embeddings to find memories similar to the query text.
    """
    from skynet.memory.models import MemoryType

    if not app_state.memory_manager:
        raise HTTPException(
            status_code=503, detail="Memory system not available"
        )

    # Generate embedding for query
    if not app_state.memory_manager.vector_indexer:
        raise HTTPException(
            status_code=503, detail="Vector search not available (no embedding provider)"
        )

    try:
        embedding = await app_state.memory_manager.vector_indexer.generate_embedding(
            request.query_text
        )
    except Exception as e:
        logger.error(f"Failed to generate embedding: {e}")
        raise HTTPException(
            status_code=500, detail=f"Embedding generation failed: {e}"
        )

    # Search similar memories
    memory_type = MemoryType(request.memory_type.value) if request.memory_type else None

    memories = await app_state.memory_manager.storage.search_similar(
        embedding=embedding,
        limit=request.limit,
        memory_type=memory_type,
    )

    # Convert to response format
    results = []
    for mem in memories:
        result = schemas.MemoryRecordResponse(
            id=mem.id,
            timestamp=mem.timestamp.isoformat(),
            memory_type=mem.memory_type.value,
            content=mem.content,
            metadata=mem.metadata,
            retrieval_count=mem.retrieval_count,
            importance_score=mem.importance_score,
        )
        results.append(result)

    logger.info(f"Similarity search: found {len(results)} memories for '{request.query_text[:50]}'")

    return schemas.SimilarMemoryResponse(
        results=results,
        count=len(results),
        query_text=request.query_text,
    )


@router.get("/memory/stats", response_model=schemas.MemoryStatsResponse)
async def memory_stats():
    """
    Get memory system statistics.

    Returns counts by type and system information.
    """
    if not app_state.memory_manager:
        raise HTTPException(
            status_code=503, detail="Memory system not available"
        )

    # Get stats
    stats = await app_state.memory_manager.get_memory_stats()

    # Determine backend
    storage_class = app_state.memory_manager.storage.__class__.__name__
    storage_backend = "postgresql" if "PostgreSQL" in storage_class else "sqlite"

    # Embedding provider
    embedding_provider = None
    if app_state.memory_manager.vector_indexer:
        embedding_provider = app_state.memory_manager.vector_indexer.__class__.__name__

    total = sum(stats.values())

    return schemas.MemoryStatsResponse(
        total_memories=total,
        by_type=stats,
        storage_backend=storage_backend,
        embedding_provider=embedding_provider,
    )


# ============================================================================
# Helper Functions
# ============================================================================


def _determine_target(step: dict, allowed_targets: list[WorkerTarget]) -> WorkerTarget:
    """Determine execution target for a step."""
    step_desc = step.get("description", "").lower()
    step_command = step.get("command", "").lower()

    # Deploy/production → EC2
    if "deploy" in step_desc or "production" in step_desc or "ec2" in step_command:
        if WorkerTarget.EC2 in allowed_targets:
            return WorkerTarget.EC2

    # Docker operations → Docker target if available
    if "docker" in step_desc or "docker" in step_command:
        if WorkerTarget.DOCKER in allowed_targets:
            return WorkerTarget.DOCKER

    # Default to laptop (local development)
    return WorkerTarget.LAPTOP if WorkerTarget.LAPTOP in allowed_targets else allowed_targets[0]


def _determine_agent(step: dict) -> str:
    """Determine agent type based on step description."""
    desc = step.get("description", "").lower()
    command = step.get("command", "").lower()

    if any(kw in desc or kw in command for kw in ["test", "pytest", "jest"]):
        return "tester"
    elif any(kw in desc or kw in command for kw in ["build", "compile", "docker build"]):
        return "builder"
    elif any(kw in desc or kw in command for kw in ["deploy", "push", "release"]):
        return "deployer"
    elif any(kw in desc or kw in command for kw in ["code", "modify", "edit", "implement"]):
        return "coder"
    elif any(kw in desc or kw in command for kw in ["git", "commit", "branch"]):
        return "git"
    else:
        return "executor"
