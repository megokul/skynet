"""
SKYNET — Shared Error Definitions

Common exceptions used across all SKYNET components.
"""


class SkyNetError(Exception):
    """Base exception for all SKYNET errors."""
    pass


# =============================================================================
# Configuration Errors
# =============================================================================
class ConfigurationError(SkyNetError):
    """Raised when required configuration is missing or invalid."""
    pass


class MissingEnvironmentVariableError(ConfigurationError):
    """Raised when a required environment variable is not set."""
    def __init__(self, var_name: str):
        self.var_name = var_name
        super().__init__(f"Missing required environment variable: {var_name}")


# =============================================================================
# Ledger Errors
# =============================================================================
class LedgerError(SkyNetError):
    """Base exception for ledger-related errors."""
    pass


class JobNotFoundError(LedgerError):
    """Raised when a job ID is not found in the ledger."""
    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"Job not found: {job_id}")


class WorkerNotFoundError(LedgerError):
    """Raised when a worker ID is not found."""
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        super().__init__(f"Worker not found: {worker_id}")


class JobLockedError(LedgerError):
    """Raised when attempting to acquire a lock on an already locked job."""
    def __init__(self, job_id: str, locked_by: str):
        self.job_id = job_id
        self.locked_by = locked_by
        super().__init__(f"Job {job_id} is already locked by {locked_by}")


# =============================================================================
# Policy Errors
# =============================================================================
class PolicyError(SkyNetError):
    """Base exception for policy-related errors."""
    pass


class PolicyViolationError(PolicyError):
    """Raised when an action violates policy rules."""
    def __init__(self, message: str, risk_level: str = "UNKNOWN"):
        self.risk_level = risk_level
        super().__init__(message)


class ApprovalRequiredError(PolicyError):
    """Raised when an action requires user approval."""
    def __init__(self, job_id: str, risk_level: str):
        self.job_id = job_id
        self.risk_level = risk_level
        super().__init__(f"Job {job_id} requires approval (risk: {risk_level})")


# =============================================================================
# Protocol Errors
# =============================================================================
class ProtocolError(SkyNetError):
    """Base exception for protocol-related errors."""
    pass


class InvalidExecutionSpecError(ProtocolError):
    """Raised when an ExecutionSpec is invalid."""
    pass


class ValidationError(ProtocolError):
    """Raised when validation fails."""
    pass


# =============================================================================
# Provider Errors
# =============================================================================
class ProviderError(SkyNetError):
    """Base exception for provider-related errors."""
    pass


class ProviderNotFoundError(ProviderError):
    """Raised when a requested provider is not registered."""
    def __init__(self, provider_name: str):
        self.provider_name = provider_name
        super().__init__(f"Provider not found: {provider_name}")


class ProviderUnavailableError(ProviderError):
    """Raised when a provider is temporarily unavailable."""
    def __init__(self, provider_name: str, reason: str = ""):
        self.provider_name = provider_name
        self.reason = reason
        super().__init__(f"Provider {provider_name} unavailable: {reason}")


class ExecutionError(ProviderError):
    """Raised when execution fails."""
    def __init__(self, job_id: str, message: str, exit_code: int = -1):
        self.job_id = job_id
        self.exit_code = exit_code
        super().__init__(f"Execution failed for job {job_id}: {message}")


# =============================================================================
# Queue Errors
# =============================================================================
class QueueError(SkyNetError):
    """Base exception for queue-related errors."""
    pass


class QueueFullError(QueueError):
    """Raised when the queue is at capacity."""
    pass


class JobCancelledError(QueueError):
    """Raised when a job has been cancelled."""
    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"Job {job_id} has been cancelled")


# =============================================================================
# Worker Errors
# =============================================================================
class WorkerError(SkyNetError):
    """Base exception for worker-related errors."""
    pass


class WorkerOfflineError(WorkerError):
    """Raised when a worker is not connected."""
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        super().__init__(f"Worker {worker_id} is offline")


class WorkerTimeoutError(WorkerError):
    """Raised when a worker operation times out."""
    def __init__(self, worker_id: str, operation: str):
        self.worker_id = worker_id
        self.operation = operation
        super().__init__(f"Worker {worker_id} timed out during {operation}")
