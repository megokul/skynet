"""
CHATHAN Providers — Abstract Base Provider

All execution providers inherit from BaseExecutionProvider and implement
the execute/health_check/cancel interface.  This makes the execution
backend pluggable — CHATHAN Worker (via WebSocket) is Provider #1,
but Docker, local, or K8s providers can be added later.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from chathan.protocol.execution_spec import ExecutionSpec


@dataclass
class ExecutionResult:
    """Result returned by any execution provider."""

    job_id: str = ""
    status: str = "pending"        # pending | running | succeeded | failed | cancelled
    logs: str = ""
    artifacts: list[str] = field(default_factory=list)
    exit_code: int = -1
    error: str = ""
    step_results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """
        Succeeded.
        
        Purpose:
        - Implement `succeeded` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `bool` when available; otherwise side effects only.
        """

        return self.status == "succeeded"

    def to_dict(self) -> dict[str, Any]:
        """
        To dict.
        
        Purpose:
        - Implement `to_dict` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        return {
            "job_id": self.job_id,
            "status": self.status,
            "logs": self.logs,
            "artifacts": self.artifacts,
            "exit_code": self.exit_code,
            "error": self.error,
            "step_results": self.step_results,
        }


class BaseExecutionProvider(ABC):
    """
    Abstract execution provider.

    Each provider knows how to execute an ExecutionSpec on a particular
    backend (CHATHAN Worker, Docker container, local shell, etc.).
    """

    name: str = "base"

    @abstractmethod
    async def execute(self, spec: ExecutionSpec) -> ExecutionResult:
        """Execute the spec and return the result."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the provider is available and healthy."""
        ...

    @abstractmethod
    async def cancel(self, job_id: str) -> bool:
        """Cancel a running job. Returns True if cancellation was accepted."""
        ...
