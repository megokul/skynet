"""
Provider Scoring — Algorithms for scoring and ranking execution providers.

Scores providers based on multiple factors:
- Health status (from ProviderMonitor)
- Current load (from WorkerRegistry)
- Capability match (from capabilities matrix)
- Historical success rate (from MemoryManager)
- Latency/performance (from past executions)

Final score is weighted combination of all factors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ProviderScore:
    """
    Multi-factor score for a provider.

    Attributes:
        provider_name: Name of the provider
        health_score: Health status score (0.0-1.0)
        load_score: Load/capacity score (0.0-1.0)
        capability_score: Capability match score (0.0-1.0)
        success_score: Historical success rate (0.0-1.0)
        latency_score: Performance/latency score (0.0-1.0)
        total_score: Weighted total (0.0-1.0)
    """

    provider_name: str
    health_score: float
    load_score: float
    capability_score: float
    success_score: float = 0.5  # Default: unknown success rate
    latency_score: float = 0.5  # Default: unknown latency
    total_score: float = 0.0

    # Scoring weights (must sum to 1.0)
    HEALTH_WEIGHT = 0.30  # Health is critical
    LOAD_WEIGHT = 0.25  # Avoid overloaded providers
    CAPABILITY_WEIGHT = 0.25  # Must be able to execute the task
    SUCCESS_WEIGHT = 0.15  # Learn from past performance
    LATENCY_WEIGHT = 0.05  # Prefer faster providers (minor factor)

    @classmethod
    def calculate(
        cls,
        provider_name: str,
        health_score: float,
        load_score: float,
        capability_score: float,
        success_score: float = 0.5,
        latency_score: float = 0.5,
    ) -> ProviderScore:
        """
        Calculate weighted total score.

        Args:
            provider_name: Provider name
            health_score: Health status (0.0-1.0)
            load_score: Load capacity (0.0-1.0)
            capability_score: Capability match (0.0-1.0)
            success_score: Historical success rate (0.0-1.0)
            latency_score: Performance score (0.0-1.0)

        Returns:
            ProviderScore with total_score calculated
        """
        total = (
            health_score * cls.HEALTH_WEIGHT
            + load_score * cls.LOAD_WEIGHT
            + capability_score * cls.CAPABILITY_WEIGHT
            + success_score * cls.SUCCESS_WEIGHT
            + latency_score * cls.LATENCY_WEIGHT
        )

        return cls(
            provider_name=provider_name,
            health_score=health_score,
            load_score=load_score,
            capability_score=capability_score,
            success_score=success_score,
            latency_score=latency_score,
            total_score=total,
        )

    def __repr__(self) -> str:
        """String representation for logging."""
        return (
            f"ProviderScore({self.provider_name}: "
            f"total={self.total_score:.2f}, "
            f"health={self.health_score:.2f}, "
            f"load={self.load_score:.2f}, "
            f"capability={self.capability_score:.2f}, "
            f"success={self.success_score:.2f}, "
            f"latency={self.latency_score:.2f})"
        )


# ============================================================================
# Individual Scoring Functions
# ============================================================================


def calculate_health_score(provider_health: dict[str, Any]) -> float:
    """
    Calculate health score from provider health status.

    Args:
        provider_health: Health status dict from ProviderMonitor
            Expected fields:
            - is_healthy: bool
            - status: str (healthy/degraded/unhealthy)
            - error_count: int (optional)
            - uptime_percentage: float (optional)

    Returns:
        Health score (0.0-1.0)
        - 1.0 = healthy
        - 0.5 = degraded
        - 0.0 = unhealthy
    """
    # Simple boolean check
    is_healthy = provider_health.get("is_healthy", False)
    if isinstance(is_healthy, bool):
        return 1.0 if is_healthy else 0.0

    # Status-based scoring
    status = provider_health.get("status", "unknown").lower()
    if status == "healthy":
        return 1.0
    elif status == "degraded":
        return 0.5
    elif status == "unhealthy":
        return 0.0

    # Fallback: use uptime percentage if available
    uptime = provider_health.get("uptime_percentage")
    if uptime is not None:
        return min(1.0, max(0.0, uptime / 100.0))

    # Unknown health = assume degraded
    return 0.5


def calculate_load_score(current_load: int, max_load: int = 10) -> float:
    """
    Calculate load score based on current worker load.

    Args:
        current_load: Number of currently executing jobs
        max_load: Maximum recommended concurrent jobs

    Returns:
        Load score (0.0-1.0)
        - 1.0 = no load (0 jobs)
        - 0.5 = half loaded (5 jobs if max=10)
        - 0.0 = fully loaded (>= max jobs)
    """
    if current_load <= 0:
        return 1.0  # No load = perfect

    if current_load >= max_load:
        return 0.0  # At or over capacity

    # Linear penalty based on load
    return max(0.0, 1.0 - (current_load / max_load))


def calculate_success_score(
    success_count: int, failure_count: int, min_samples: int = 3
) -> float:
    """
    Calculate historical success rate score.

    Uses Laplace smoothing to handle low sample counts.

    Args:
        success_count: Number of successful executions
        failure_count: Number of failed executions
        min_samples: Minimum samples for confidence (default: 3)

    Returns:
        Success score (0.0-1.0)
    """
    total = success_count + failure_count

    if total == 0:
        return 0.5  # No history = neutral

    # Laplace smoothing: add 1 success and 1 failure to prevent extreme scores
    smoothed_success = success_count + 1
    smoothed_total = total + 2

    success_rate = smoothed_success / smoothed_total

    # Apply confidence penalty for low sample counts
    if total < min_samples:
        confidence = total / min_samples
        # Blend with neutral (0.5) based on confidence
        success_rate = (success_rate * confidence) + (0.5 * (1 - confidence))

    return success_rate


def calculate_latency_score(
    avg_duration_seconds: float, target_duration: float = 60.0
) -> float:
    """
    Calculate performance/latency score.

    Args:
        avg_duration_seconds: Average execution duration
        target_duration: Target duration for good performance (default: 60s)

    Returns:
        Latency score (0.0-1.0)
        - 1.0 = faster than target
        - 0.5 = at target duration
        - 0.0 = much slower than target (2x or more)
    """
    if avg_duration_seconds <= 0:
        return 0.5  # Unknown = neutral

    if avg_duration_seconds <= target_duration:
        # Faster than target = full score
        return 1.0

    # Penalty for being slower than target
    # 2x target = 0.0 score
    ratio = avg_duration_seconds / target_duration
    score = max(0.0, 2.0 - ratio)  # Linear penalty from 1.0 to 0.0

    return score


# ============================================================================
# Combined Scoring Function
# ============================================================================


def score_provider(
    provider_name: str,
    provider_health: dict[str, Any],
    current_load: int,
    capability_match: float,
    success_count: int = 0,
    failure_count: int = 0,
    avg_duration: float = 0.0,
) -> ProviderScore:
    """
    Calculate comprehensive provider score.

    Args:
        provider_name: Provider name
        provider_health: Health status dict
        current_load: Current number of jobs
        capability_match: Capability match score (0.0-1.0)
        success_count: Historical success count
        failure_count: Historical failure count
        avg_duration: Average execution duration (seconds)

    Returns:
        ProviderScore with all factors
    """
    health_score = calculate_health_score(provider_health)
    load_score = calculate_load_score(current_load)
    success_score = calculate_success_score(success_count, failure_count)
    latency_score = calculate_latency_score(avg_duration)

    return ProviderScore.calculate(
        provider_name=provider_name,
        health_score=health_score,
        load_score=load_score,
        capability_score=capability_match,
        success_score=success_score,
        latency_score=latency_score,
    )
