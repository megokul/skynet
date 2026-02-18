"""
SKYNET API Schemas - Pydantic models for FastAPI endpoints.

Defines request/response models for:
- POST /v1/plan - Generate execution plan
- POST /v1/report - Receive progress updates
- POST /v1/policy/check - Policy validation
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ============================================================================
# Enums
# ============================================================================


class ExecutionMode(str, Enum):
    """Decision mode for execution."""

    EXECUTE = "execute"  # Proceed with execution
    CLARIFY = "clarify"  # Need more information from user
    REFUSE = "refuse"  # Cannot execute (policy violation)


class RiskLevel(str, Enum):
    """Risk classification for tasks."""

    LOW = "low"  # Read-only operations
    MEDIUM = "med"  # Write operations
    HIGH = "high"  # Admin/deployment operations


class ProviderType(str, Enum):
    """AI provider types for cost optimization."""

    LOCAL = "local"  # Ollama local models
    FREE_API = "free_api"  # Gemini/Groq free tier
    PAID_API = "paid_api"  # OpenAI/Anthropic paid


class Environment(str, Enum):
    """Deployment environment."""

    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class WorkerTarget(str, Enum):
    """Execution target for tasks."""

    LAPTOP = "laptop"  # Local development machine
    EC2 = "ec2"  # AWS EC2 worker
    DOCKER = "docker"  # Docker container


class StepStatus(str, Enum):
    """Status of execution step."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ============================================================================
# POST /v1/plan - Request Models
# ============================================================================


class TaskContext(BaseModel):
    """Context information for task planning."""

    repo: str | None = Field(None, description="Git repository name/URL")
    branch: str | None = Field(None, description="Git branch")
    environment: Environment = Field(
        Environment.DEV, description="Deployment environment"
    )
    recent_actions: list[dict[str, Any]] = Field(
        default_factory=list, description="Recent task history"
    )


class TaskConstraints(BaseModel):
    """Budget and safety constraints for task execution."""

    max_cost_usd: float = Field(1.50, description="Maximum cost in USD")
    time_budget_min: int = Field(30, description="Time budget in minutes")
    allowed_targets: list[WorkerTarget] = Field(
        default_factory=lambda: [WorkerTarget.LAPTOP],
        description="Allowed execution targets",
    )
    requires_approval_for: list[str] = Field(
        default_factory=lambda: ["deploy_prod", "send_email"],
        description="Actions requiring approval gates",
    )


class PlanRequest(BaseModel):
    """Request to generate an execution plan."""

    request_id: UUID = Field(..., description="Unique request identifier")
    user_message: str = Field(..., description="User's task description")
    context: TaskContext = Field(
        default_factory=TaskContext, description="Task context"
    )
    constraints: TaskConstraints = Field(
        default_factory=TaskConstraints, description="Execution constraints"
    )


# ============================================================================
# POST /v1/plan - Response Models
# ============================================================================


class ModelPolicy(BaseModel):
    """AI provider routing policy for cost optimization."""

    default: ProviderType = Field(
        ProviderType.LOCAL, description="Default provider"
    )
    escalation: list[ProviderType] = Field(
        default_factory=lambda: [ProviderType.FREE_API, ProviderType.PAID_API],
        description="Escalation chain",
    )


class ExecutionDecision(BaseModel):
    """Decision on how to handle the request."""

    mode: ExecutionMode = Field(..., description="Execution mode")
    risk_level: RiskLevel = Field(..., description="Risk classification")
    model_policy: ModelPolicy = Field(..., description="AI provider policy")
    reason: str | None = Field(None, description="Explanation for decision")


class ExecutionStep(BaseModel):
    """Single step in execution plan."""

    step: int = Field(..., description="Step number (1-indexed)")
    agent: str = Field(..., description="Agent type (coder, tester, builder, etc.)")
    action: str = Field(..., description="Action to perform")
    target: WorkerTarget = Field(..., description="Execution target")
    description: str | None = Field(None, description="Human-readable description")
    estimated_time_min: int | None = Field(None, description="Estimated time in minutes")


class ApprovalGate(BaseModel):
    """Approval gate for sensitive operations."""

    gate: str = Field(..., description="Gate identifier")
    required: bool = Field(True, description="Is approval required?")
    when_step: int = Field(..., description="Step number requiring approval")
    reason: str | None = Field(None, description="Why approval is needed")


class ArtifactConfig(BaseModel):
    """Configuration for artifact storage."""

    s3_prefix: str = Field(..., description="S3 path prefix for artifacts")
    retention_days: int = Field(30, description="Artifact retention period")


class PlanResponse(BaseModel):
    """Response containing execution plan."""

    request_id: UUID = Field(..., description="Request identifier")
    decision: ExecutionDecision = Field(..., description="Execution decision")
    execution_plan: list[ExecutionStep] = Field(
        default_factory=list, description="Ordered execution steps"
    )
    approval_gates: list[ApprovalGate] = Field(
        default_factory=list, description="Approval gates"
    )
    artifacts: ArtifactConfig = Field(..., description="Artifact configuration")


# ============================================================================
# POST /v1/report - Request/Response Models
# ============================================================================


class StepReport(BaseModel):
    """Progress report for a single step."""

    step: int = Field(..., description="Step number")
    status: StepStatus = Field(..., description="Step status")
    started_at: str | None = Field(None, description="ISO 8601 timestamp")
    completed_at: str | None = Field(None, description="ISO 8601 timestamp")
    output: str | None = Field(None, description="Step output/logs")
    error: str | None = Field(None, description="Error message if failed")
    artifacts_uploaded: list[str] = Field(
        default_factory=list, description="Uploaded artifact paths"
    )


class ReportRequest(BaseModel):
    """Progress report from OpenClaw."""

    request_id: UUID = Field(..., description="Request identifier")
    step_reports: list[StepReport] = Field(..., description="Step progress reports")
    overall_status: StepStatus = Field(..., description="Overall job status")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class ReportResponse(BaseModel):
    """Acknowledgment of progress report."""

    request_id: UUID = Field(..., description="Request identifier")
    received: bool = Field(True, description="Report received successfully")
    next_action: str | None = Field(None, description="Suggested next action")


# ============================================================================
# POST /v1/policy/check - Request/Response Models
# ============================================================================


class PolicyCheckRequest(BaseModel):
    """Request to check if action is allowed."""

    action: str = Field(..., description="Action to check (e.g., 'deploy_prod')")
    target: WorkerTarget | None = Field(None, description="Target worker")
    context: dict[str, Any] = Field(
        default_factory=dict, description="Additional context"
    )


class PolicyCheckResponse(BaseModel):
    """Result of policy check."""

    allowed: bool = Field(..., description="Is action allowed?")
    reason: str = Field(..., description="Explanation")
    requires_approval: bool = Field(
        False, description="Does this require human approval?"
    )
    risk_level: RiskLevel = Field(..., description="Risk classification")


# ============================================================================
# Memory API - Request/Response Models (SKYNET 2.0)
# ============================================================================


class MemoryTypeEnum(str, Enum):
    """Memory types for filtering."""

    TASK_EXECUTION = "task_execution"
    FAILURE_PATTERN = "failure_pattern"
    SUCCESS_STRATEGY = "success_strategy"
    SYSTEM_STATE = "system_state"
    USER_PREFERENCE = "user_preference"


class StoreMemoryRequest(BaseModel):
    """Request to manually store a memory."""

    memory_type: MemoryTypeEnum = Field(..., description="Type of memory")
    content: dict[str, Any] = Field(..., description="Memory content")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class StoreMemoryResponse(BaseModel):
    """Response after storing memory."""

    memory_id: str = Field(..., description="Unique memory identifier")
    stored_at: str = Field(..., description="ISO timestamp")


class SearchMemoryRequest(BaseModel):
    """Request to search memories."""

    query: str | None = Field(None, description="Search query")
    memory_type: MemoryTypeEnum | None = Field(None, description="Filter by type")
    limit: int = Field(10, description="Maximum results", ge=1, le=100)
    include_embeddings: bool = Field(
        False, description="Include embedding vectors in response"
    )


class MemoryRecordResponse(BaseModel):
    """Memory record in API response."""

    id: str
    timestamp: str  # ISO format
    memory_type: str
    content: dict[str, Any]
    metadata: dict[str, Any]
    retrieval_count: int
    importance_score: float
    embedding: list[float] | None = None  # Only if requested


class SearchMemoryResponse(BaseModel):
    """Response with search results."""

    results: list[MemoryRecordResponse] = Field(..., description="Found memories")
    count: int = Field(..., description="Number of results")
    query: str | None = Field(None, description="Original query")


class SimilarMemoryRequest(BaseModel):
    """Request for semantic similarity search."""

    query_text: str = Field(..., description="Text to find similar memories for")
    memory_type: MemoryTypeEnum | None = Field(None, description="Filter by type")
    limit: int = Field(5, description="Maximum results", ge=1, le=50)


class SimilarMemoryResponse(BaseModel):
    """Response with similar memories."""

    results: list[MemoryRecordResponse] = Field(..., description="Similar memories")
    count: int = Field(..., description="Number of results")
    query_text: str = Field(..., description="Original query")


class MemoryStatsResponse(BaseModel):
    """Memory system statistics."""

    total_memories: int
    by_type: dict[str, int]
    storage_backend: str  # "postgresql" or "sqlite"
    embedding_provider: str | None = None


# ============================================================================
# Health Check
# ============================================================================


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field("ok", description="Service status")
    version: str = Field("1.0.0", description="API version")
    components: dict[str, str] = Field(
        default_factory=dict, description="Component status"
    )


# ============================================================================
# Direct Execution Schemas (Phase 4)
# ============================================================================


class ExecuteRequest(BaseModel):
    """Request for direct synchronous execution."""

    execution_spec: dict[str, Any] = Field(
        ..., description="Execution specification with steps/actions"
    )
    timeout: int | None = Field(
        None,
        description="Total execution timeout in seconds (max 1800)",
        ge=1,
        le=1800,
    )


class ExecuteStepResult(BaseModel):
    """Result of a single execution step."""

    action: str = Field(..., description="Action that was executed")
    status: str = Field(..., description="Step status (success/error/timeout)")
    output: str = Field("", description="Step output")
    stdout: str = Field("", description="Standard output")
    stderr: str = Field("", description="Standard error")
    error: str | None = Field(None, description="Error message if failed")


class ExecuteResponse(BaseModel):
    """Response from direct execution."""

    job_id: str = Field(..., description="Job identifier")
    status: str = Field(
        ...,
        description="Overall status (success/partial_failure/timeout/error)",
    )
    provider: str = Field(..., description="Provider that executed the task")
    results: list[ExecuteStepResult] = Field(
        default_factory=list, description="Step-by-step results"
    )
    steps_completed: int = Field(..., description="Number of steps completed")
    steps_total: int = Field(..., description="Total number of steps")
    elapsed_seconds: float = Field(..., description="Total execution time")
    error: str | None = Field(None, description="Error message if failed")
    timeout_seconds: float | None = Field(
        None, description="Timeout value if timed out"
    )


# ============================================================================
# Scheduler Diagnostics Schemas
# ============================================================================


class SchedulerDiagnoseRequest(BaseModel):
    """Request provider selection diagnostics for an execution spec."""

    execution_spec: dict[str, Any] = Field(
        ..., description="Execution specification with actions/steps"
    )
    fallback: str = Field("local", description="Fallback provider name")


class SchedulerScoreResponse(BaseModel):
    """Per-provider scheduler score breakdown."""

    provider: str = Field(..., description="Provider name")
    total_score: float = Field(..., description="Weighted total score")
    health_score: float = Field(..., description="Health factor score")
    load_score: float = Field(..., description="Load factor score")
    capability_score: float = Field(..., description="Capability factor score")
    success_score: float = Field(..., description="Historical success factor score")
    latency_score: float = Field(..., description="Latency factor score")


class SchedulerDiagnoseResponse(BaseModel):
    """Scheduler diagnostics response."""

    selected_provider: str = Field(..., description="Chosen provider")
    fallback_used: bool = Field(..., description="Whether fallback was used")
    preselected_provider: str | None = Field(
        None, description="Preselected provider from execution spec, if any"
    )
    required_capabilities: list[str] = Field(
        default_factory=list, description="Capabilities extracted from execution spec"
    )
    candidates: list[str] = Field(
        default_factory=list, description="Capability-matching candidate providers"
    )
    scores: list[SchedulerScoreResponse] = Field(
        default_factory=list, description="Sorted provider scores (highest first)"
    )


# ============================================================================
# Provider Health Dashboard Schemas
# ============================================================================


class ProviderHealthDashboardResponse(BaseModel):
    """Provider monitor dashboard response."""

    status: str = Field(..., description="Overall provider health status")
    message: str | None = Field(
        None, description="Optional status message (e.g., no data)"
    )
    healthy_count: int = Field(0, description="Number of healthy providers")
    unhealthy_count: int = Field(0, description="Number of unhealthy providers")
    unknown_count: int = Field(0, description="Number of unknown-status providers")
    total_count: int = Field(0, description="Total providers tracked")
    providers: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Per-provider health details"
    )
    last_check: float | None = Field(
        None, description="Unix timestamp of latest health check"
    )
    history: list[dict[str, Any]] = Field(
        default_factory=list, description="Recent health snapshots"
    )
