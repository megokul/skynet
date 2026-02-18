"""
Provider Scheduler — Intelligent provider selection for task execution.

Automatically selects the best execution provider based on:
- Provider health status
- Current load and capacity
- Capability matching
- Historical success rates
- Performance metrics

This replaces the simple environment variable provider selection with
intelligent, data-driven scheduling.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from .capabilities import (
    PROVIDER_CAPABILITIES,
    calculate_capability_match,
    extract_required_capabilities,
    get_matching_providers,
)
from .scoring import score_provider, ProviderScore
from skynet.memory.models import MemoryType

if TYPE_CHECKING:
    from skynet.sentinel.provider_monitor import ProviderMonitor
    from skynet.ledger.worker_registry import WorkerRegistry
    from skynet.memory.memory_manager import MemoryManager

logger = logging.getLogger("skynet.scheduler")


class ProviderScheduler:
    """
    Intelligent scheduler for execution provider selection.

    Analyzes task requirements and system state to select the optimal
    provider for each execution.

    Usage:
        scheduler = ProviderScheduler(
            provider_monitor=monitor,
            worker_registry=registry,
            memory_manager=memory
        )

        provider = await scheduler.select_provider(execution_spec)
    """

    def __init__(
        self,
        provider_monitor: ProviderMonitor | None = None,
        worker_registry: WorkerRegistry | None = None,
        memory_manager: MemoryManager | None = None,
        available_providers: list[str] | None = None,
    ):
        """
        Initialize ProviderScheduler.

        Args:
            provider_monitor: Monitor for provider health (optional)
            worker_registry: Registry for worker load info (optional)
            memory_manager: Memory manager for historical data (optional)
            available_providers: List of available provider names (optional)
        """
        self.provider_monitor = provider_monitor
        self.worker_registry = worker_registry
        self.memory_manager = memory_manager

        # Available providers (defaults to all known providers)
        self.available_providers = available_providers or list(
            PROVIDER_CAPABILITIES.keys()
        )

        logger.info(
            f"ProviderScheduler initialized with {len(self.available_providers)} providers"
        )

    # ========================================================================
    # Main Scheduling API
    # ========================================================================

    async def select_provider(
        self, execution_spec: dict[str, Any], fallback: str = "local"
    ) -> str:
        """
        Select best provider for execution spec.

        Main entry point for intelligent provider selection.

        Args:
            execution_spec: Execution specification with actions/steps
            fallback: Fallback provider if selection fails (default: "local")

        Returns:
            Selected provider name
        """
        # Check if provider is pre-specified in execution spec
        preselected = execution_spec.get("provider")
        if preselected:
            logger.info(f"Using pre-selected provider: {preselected}")
            return preselected

        logger.info("Selecting provider for execution...")

        try:
            # Step 1: Extract required capabilities
            required_capabilities = extract_required_capabilities(execution_spec)
            logger.debug(f"Required capabilities: {required_capabilities}")

            # Step 2: Get candidate providers (those with required capabilities)
            candidates = self._get_candidate_providers(required_capabilities)

            if not candidates:
                logger.warning(
                    f"No providers match capabilities {required_capabilities}, "
                    f"using fallback: {fallback}"
                )
                return fallback

            logger.debug(f"Candidate providers: {candidates}")

            # Step 3: Score each candidate
            scored_providers = await self._score_providers(
                candidates, required_capabilities, execution_spec
            )

            if not scored_providers:
                logger.warning(f"Scoring failed, using fallback: {fallback}")
                return fallback

            # Step 4: Pick best provider
            best_provider = self._pick_best(scored_providers)

            logger.info(
                f"Selected provider: {best_provider.provider_name} "
                f"(score={best_provider.total_score:.2f})"
            )

            return best_provider.provider_name

        except Exception as e:
            logger.error(f"Provider selection failed: {e}, using fallback: {fallback}")
            return fallback

    async def diagnose_selection(
        self, execution_spec: dict[str, Any], fallback: str = "local"
    ) -> dict[str, Any]:
        """
        Return provider selection diagnostics for observability/debugging.

        Args:
            execution_spec: Execution specification with actions/steps
            fallback: Fallback provider if no candidates or scoring fails

        Returns:
            Diagnostics dictionary with candidates, scores, and selected provider
        """
        preselected = execution_spec.get("provider")
        if preselected:
            return {
                "selected_provider": preselected,
                "fallback_used": False,
                "preselected_provider": preselected,
                "required_capabilities": [],
                "candidates": [preselected],
                "scores": [],
            }

        required_capabilities = extract_required_capabilities(execution_spec)
        candidates = self._get_candidate_providers(required_capabilities)

        if not candidates:
            return {
                "selected_provider": fallback,
                "fallback_used": True,
                "preselected_provider": None,
                "required_capabilities": required_capabilities,
                "candidates": [],
                "scores": [],
            }

        scored_providers = await self._score_providers(
            candidates, required_capabilities, execution_spec
        )
        sorted_scores = sorted(scored_providers, key=lambda p: p.total_score, reverse=True)

        if not sorted_scores:
            return {
                "selected_provider": fallback,
                "fallback_used": True,
                "preselected_provider": None,
                "required_capabilities": required_capabilities,
                "candidates": candidates,
                "scores": [],
            }

        best = sorted_scores[0]
        return {
            "selected_provider": best.provider_name,
            "fallback_used": False,
            "preselected_provider": None,
            "required_capabilities": required_capabilities,
            "candidates": candidates,
            "scores": [
                {
                    "provider": p.provider_name,
                    "total_score": p.total_score,
                    "health_score": p.health_score,
                    "load_score": p.load_score,
                    "capability_score": p.capability_score,
                    "success_score": p.success_score,
                    "latency_score": p.latency_score,
                }
                for p in sorted_scores
            ],
        }

    # ========================================================================
    # Candidate Selection
    # ========================================================================

    def _get_candidate_providers(
        self, required_capabilities: list[str]
    ) -> list[str]:
        """
        Get providers that match required capabilities.

        Args:
            required_capabilities: List of required capability names

        Returns:
            List of provider names that have the capabilities
        """
        # Filter by capability match
        matching = get_matching_providers(required_capabilities)

        # Further filter by availability
        candidates = [p for p in matching if p in self.available_providers]

        return candidates

    # ========================================================================
    # Provider Scoring
    # ========================================================================

    async def _score_providers(
        self,
        candidates: list[str],
        required_capabilities: list[str],
        execution_spec: dict[str, Any],
    ) -> list[ProviderScore]:
        """
        Score all candidate providers.

        Args:
            candidates: List of candidate provider names
            required_capabilities: Required capabilities
            execution_spec: Execution specification

        Returns:
            List of ProviderScore objects
        """
        scored = []

        for provider_name in candidates:
            try:
                score = await self._score_single_provider(
                    provider_name, required_capabilities, execution_spec
                )
                scored.append(score)
            except Exception as e:
                logger.warning(f"Failed to score provider {provider_name}: {e}")

        return scored

    async def _score_single_provider(
        self,
        provider_name: str,
        required_capabilities: list[str],
        execution_spec: dict[str, Any],
    ) -> ProviderScore:
        """
        Score a single provider.

        Args:
            provider_name: Provider name
            required_capabilities: Required capabilities
            execution_spec: Execution specification

        Returns:
            ProviderScore
        """
        # 1. Capability match score
        capability_score = calculate_capability_match(
            provider_name, required_capabilities
        )

        # 2. Health score (from ProviderMonitor)
        provider_health = {"is_healthy": True}  # Default: assume healthy
        if self.provider_monitor:
            try:
                provider_health = await self._get_provider_health(provider_name)
            except Exception as e:
                logger.debug(f"Failed to get health for {provider_name}: {e}")

        # 3. Load score (from WorkerRegistry)
        current_load = 0  # Default: no load
        if self.worker_registry:
            try:
                current_load = await self._get_provider_load(provider_name)
            except Exception as e:
                logger.debug(f"Failed to get load for {provider_name}: {e}")

        # 4. Historical performance (from MemoryManager)
        success_count = 0
        failure_count = 0
        avg_duration = 0.0
        if self.memory_manager:
            try:
                success_count, failure_count, avg_duration = (
                    await self._get_provider_history(provider_name)
                )
            except Exception as e:
                logger.debug(f"Failed to get history for {provider_name}: {e}")

        # Calculate final score
        return score_provider(
            provider_name=provider_name,
            provider_health=provider_health,
            current_load=current_load,
            capability_match=capability_score,
            success_count=success_count,
            failure_count=failure_count,
            avg_duration=avg_duration,
        )

    async def _get_provider_health(self, provider_name: str) -> dict[str, Any]:
        """
        Get provider health status.

        Args:
            provider_name: Provider name

        Returns:
            Health status dict
        """
        if not self.provider_monitor:
            return {"is_healthy": True, "status": "healthy"}

        health = self.provider_monitor.get_provider_health(provider_name)

        # If provider was not checked yet, run an on-demand check when possible.
        if not health and provider_name in self.provider_monitor.providers:
            provider = self.provider_monitor.providers[provider_name]
            health = await self.provider_monitor.check_provider(provider_name, provider)

        if not health:
            return {"is_healthy": True, "status": "healthy"}

        status = str(health.status).lower()
        return {
            "status": status,
            "is_healthy": status == "healthy",
            "latency_ms": health.latency_ms,
            "consecutive_failures": health.consecutive_failures,
        }

    async def _get_provider_load(self, provider_name: str) -> int:
        """
        Get current provider load (number of active jobs).

        Args:
            provider_name: Provider name

        Returns:
            Number of active jobs
        """
        if not self.worker_registry:
            return 0

        # Prefer direct DB count for busy workers when available.
        db = getattr(self.worker_registry, "db", None)
        if db is not None:
            async with db.execute(
                """
                SELECT COUNT(*) as active_jobs
                FROM workers
                WHERE provider_name = ?
                  AND (
                    status = 'busy'
                    OR (status = 'online' AND current_job_id IS NOT NULL)
                  )
                """,
                (provider_name,),
            ) as cur:
                row = await cur.fetchone()
                if row:
                    return int(row["active_jobs"])

        workers = await self.worker_registry.get_online_workers(provider_name=provider_name)
        return sum(1 for w in workers if w.get("current_job_id"))

    async def _get_provider_history(
        self, provider_name: str
    ) -> tuple[int, int, float]:
        """
        Get historical performance for provider.

        Args:
            provider_name: Provider name

        Returns:
            Tuple of (success_count, failure_count, avg_duration_seconds)
        """
        if not self.memory_manager:
            return (0, 0, 0.0)

        memories = await self.memory_manager.storage.search_memories(
            memory_type=MemoryType.TASK_EXECUTION,
            limit=500,
        )

        provider_memories = [
            m for m in memories
            if str(m.content.get("provider", "")).lower() == provider_name.lower()
        ]

        if not provider_memories:
            return (0, 0, 0.0)

        success_count = sum(1 for m in provider_memories if bool(m.content.get("success", False)))
        failure_count = len(provider_memories) - success_count

        durations = []
        for memory in provider_memories:
            raw_duration = memory.content.get("duration_seconds")
            if isinstance(raw_duration, (int, float)) and raw_duration >= 0:
                durations.append(float(raw_duration))

        avg_duration = sum(durations) / len(durations) if durations else 0.0
        return (success_count, failure_count, avg_duration)

    # ========================================================================
    # Selection Logic
    # ========================================================================

    def _pick_best(self, scored_providers: list[ProviderScore]) -> ProviderScore:
        """
        Pick the best provider from scored list.

        Args:
            scored_providers: List of ProviderScore objects

        Returns:
            Best ProviderScore

        Raises:
            ValueError: If no providers available
        """
        if not scored_providers:
            raise ValueError("No providers available for selection")

        # Sort by total score (descending)
        sorted_providers = sorted(
            scored_providers, key=lambda p: p.total_score, reverse=True
        )

        # Log top 3 for debugging
        for i, provider in enumerate(sorted_providers[:3], 1):
            logger.debug(f"  #{i}: {provider}")

        return sorted_providers[0]

    # ========================================================================
    # Utility Methods
    # ========================================================================

    def get_available_providers(self) -> list[str]:
        """Get list of available provider names."""
        return list(self.available_providers)

    def add_provider(self, provider_name: str) -> None:
        """Add a provider to available list."""
        if provider_name not in self.available_providers:
            self.available_providers.append(provider_name)
            logger.info(f"Added provider: {provider_name}")

    def remove_provider(self, provider_name: str) -> None:
        """Remove a provider from available list."""
        if provider_name in self.available_providers:
            self.available_providers.remove(provider_name)
            logger.info(f"Removed provider: {provider_name}")

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"ProviderScheduler("
            f"providers={len(self.available_providers)}, "
            f"monitor={'✓' if self.provider_monitor else '✗'}, "
            f"registry={'✓' if self.worker_registry else '✗'}, "
            f"memory={'✓' if self.memory_manager else '✗'})"
        )
