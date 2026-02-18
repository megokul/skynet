"""
SKYNET Scheduler — Intelligent provider selection and load balancing.

Automatically selects the best execution provider based on:
- Provider health status
- Current load and capacity
- Capability matching
- Historical success rates

Core Components:
- ProviderScheduler: Main scheduling logic
- ProviderScoring: Scoring algorithms for provider selection
- ProviderCapabilities: Capability matrix for provider matching
- LoadBalancer: Round-robin and weighted selection strategies

Usage:
    from skynet.scheduler import ProviderScheduler

    scheduler = ProviderScheduler(
        provider_monitor=monitor,
        worker_registry=registry,
        memory_manager=memory
    )

    provider = await scheduler.select_provider(execution_spec)
"""

from .scheduler import ProviderScheduler
from .capabilities import PROVIDER_CAPABILITIES, check_capability
from .scoring import score_provider, ProviderScore

__all__ = [
    "ProviderScheduler",
    "PROVIDER_CAPABILITIES",
    "check_capability",
    "score_provider",
    "ProviderScore",
]
