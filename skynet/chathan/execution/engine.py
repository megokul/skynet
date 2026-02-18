"""
CHATHAN Execution Engine

Central dispatcher that routes ExecutionSpecs to the appropriate
provider.  Validates specs through the PolicyEngine before execution.
"""

from __future__ import annotations

import logging
from typing import Any

from skynet.chathan.protocol.execution_spec import ExecutionSpec
from skynet.chathan.providers.base_provider import BaseExecutionProvider, ExecutionResult
from skynet.policy.engine import PolicyEngine

logger = logging.getLogger("skynet.execution")


class ExecutionEngine:
    """
    Routes ExecutionSpecs to registered providers.

    Providers are registered by name (e.g. "chathan", "local", "docker").
    The spec's ``provider`` field determines which backend handles it.
    """

    def __init__(self, policy_engine: PolicyEngine | None = None):
        self._providers: dict[str, BaseExecutionProvider] = {}
        self.policy = policy_engine or PolicyEngine()

    def register(self, provider: BaseExecutionProvider) -> None:
        """Register an execution provider."""
        self._providers[provider.name] = provider
        logger.info("Registered execution provider: %s", provider.name)

    def get_provider(self, name: str) -> BaseExecutionProvider | None:
        """Get a registered provider by name."""
        return self._providers.get(name)

    @property
    def available_providers(self) -> list[str]:
        """List names of all registered providers."""
        return list(self._providers.keys())

    async def execute(self, spec: ExecutionSpec) -> ExecutionResult:
        """
        Validate and execute an ExecutionSpec.

        1. Check policy rules
        2. Resolve provider
        3. Dispatch to provider
        """
        # Policy check.
        decision = self.policy.validate_execution(spec)
        if not decision.allowed:
            logger.warning(
                "Policy blocked execution for job %s: %s",
                spec.job_id, decision.reasons,
            )
            return ExecutionResult(
                job_id=spec.job_id,
                status="failed",
                error=f"Policy violation: {'; '.join(decision.reasons)}",
                exit_code=1,
            )

        # Resolve provider.
        provider = self._providers.get(spec.provider)
        if provider is None:
            available = ", ".join(self._providers.keys()) or "(none)"
            return ExecutionResult(
                job_id=spec.job_id,
                status="failed",
                error=f"Unknown provider '{spec.provider}'. Available: {available}",
                exit_code=1,
            )

        logger.info(
            "Dispatching job %s to provider '%s' (%d steps)",
            spec.job_id, spec.provider, len(spec.steps),
        )
        return await provider.execute(spec)

    async def health_check(self) -> dict[str, bool]:
        """Check health of all registered providers."""
        results: dict[str, bool] = {}
        for name, provider in self._providers.items():
            try:
                results[name] = await provider.health_check()
            except Exception:
                results[name] = False
        return results

    async def cancel(self, job_id: str, provider_name: str | None = None) -> bool:
        """Cancel a job on a specific or all providers."""
        if provider_name:
            provider = self._providers.get(provider_name)
            if provider:
                return await provider.cancel(job_id)
            return False

        # Try all providers.
        for provider in self._providers.values():
            if await provider.cancel(job_id):
                return True
        return False
