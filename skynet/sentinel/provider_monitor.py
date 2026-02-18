"""
SKYNET Sentinel — Provider Health Monitor

Monitors the health of all execution providers:
  - MockProvider
  - LocalProvider
  - ChathanProvider (OpenClaw Gateway)
  - DockerProvider
  - SSHProvider

Provides:
  - Periodic health checks
  - Provider status tracking
  - Health history
  - Dashboard data
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("skynet.sentinel.provider")


@dataclass
class ProviderHealth:
    """Health status for a single provider."""

    provider_name: str
    status: str  # healthy | unhealthy | unknown
    message: str = ""
    latency_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    last_checked: float = field(default_factory=time.time)
    consecutive_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "status": self.status,
            "message": self.message,
            "latency_ms": round(self.latency_ms, 1),
            "details": self.details,
            "last_checked": self.last_checked,
            "consecutive_failures": self.consecutive_failures,
        }


class ProviderMonitor:
    """
    Monitors health of all execution providers.

    Features:
      - Periodic health checks via provider.health_check()
      - Status tracking with history
      - Consecutive failure counting
      - Dashboard data generation
    """

    def __init__(self, providers: dict[str, Any], check_interval: int = 60):
        """
        Initialize provider monitor.

        Args:
            providers: Dict of provider_name -> provider_instance
            check_interval: Seconds between health checks (default 60)
        """
        self.providers = providers
        self.check_interval = check_interval
        self._health_status: dict[str, ProviderHealth] = {}
        self._health_history: list[dict[str, Any]] = []
        self._running = False
        self._task: asyncio.Task | None = None

    async def check_provider(self, provider_name: str, provider: Any) -> ProviderHealth:
        """
        Check health of a single provider.

        Args:
            provider_name: Provider name
            provider: Provider instance

        Returns:
            ProviderHealth status
        """
        health = ProviderHealth(provider_name=provider_name, status="unknown")
        start = time.monotonic()

        try:
            # Call provider's health_check method
            if not hasattr(provider, "health_check"):
                health.status = "unknown"
                health.message = "Provider does not implement health_check()"
                return health

            # Run health check (may be sync or async)
            result = provider.health_check()
            if asyncio.iscoroutine(result):
                result = await result

            health.latency_ms = (time.monotonic() - start) * 1000

            # Parse result
            if isinstance(result, dict):
                provider_status = result.get("status", "unknown")
                if provider_status == "healthy":
                    health.status = "healthy"
                    health.message = "OK"
                    health.consecutive_failures = 0
                else:
                    health.status = "unhealthy"
                    health.message = result.get("error", result.get("message", "Unknown error"))
                    # Increment consecutive failures
                    prev_health = self._health_status.get(provider_name)
                    health.consecutive_failures = (
                        prev_health.consecutive_failures + 1 if prev_health else 1
                    )

                health.details = {k: v for k, v in result.items() if k not in ("status", "provider")}
            else:
                health.status = "unknown"
                health.message = f"Unexpected health_check result: {type(result)}"

        except Exception as e:
            health.latency_ms = (time.monotonic() - start) * 1000
            health.status = "unhealthy"
            health.message = f"Health check failed: {e}"
            prev_health = self._health_status.get(provider_name)
            health.consecutive_failures = (
                prev_health.consecutive_failures + 1 if prev_health else 1
            )
            logger.error(f"Provider {provider_name} health check failed: {e}")

        return health

    async def check_all_providers(self) -> dict[str, ProviderHealth]:
        """
        Check health of all providers.

        Returns:
            Dict of provider_name -> ProviderHealth
        """
        logger.info(f"Checking health of {len(self.providers)} providers...")

        # Check all providers concurrently
        tasks = []
        for name, provider in self.providers.items():
            tasks.append(self.check_provider(name, provider))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Update health status
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Provider health check error: {result}")
                continue

            if isinstance(result, ProviderHealth):
                self._health_status[result.provider_name] = result

        # Log summary
        healthy_count = sum(1 for h in self._health_status.values() if h.status == "healthy")
        total_count = len(self._health_status)
        logger.info(f"Provider health: {healthy_count}/{total_count} healthy")

        # Record in history
        self._health_history.append({
            "timestamp": time.time(),
            "providers": {name: health.to_dict() for name, health in self._health_status.items()},
            "healthy_count": healthy_count,
            "total_count": total_count,
        })

        # Keep history bounded (last 100 entries)
        if len(self._health_history) > 100:
            self._health_history = self._health_history[-100:]

        return self._health_status

    async def monitor_loop(self):
        """Background monitoring loop."""
        logger.info(f"Provider monitor started (check interval: {self.check_interval}s)")

        while self._running:
            try:
                await self.check_all_providers()
            except Exception as e:
                logger.error(f"Provider monitor error: {e}")

            # Wait for next check
            await asyncio.sleep(self.check_interval)

        logger.info("Provider monitor stopped")

    def start(self):
        """Start background monitoring."""
        if self._running:
            logger.warning("Provider monitor already running")
            return

        self._running = True
        self._task = asyncio.create_task(self.monitor_loop())
        logger.info("Provider monitor task created")

    async def stop(self):
        """Stop background monitoring."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("Provider monitor stopped")

    def get_status(self) -> dict[str, Any]:
        """
        Get current provider health status.

        Returns:
            Dict with provider health summary
        """
        if not self._health_status:
            return {
                "status": "no_data",
                "message": "No health checks performed yet",
                "providers": {},
            }

        healthy_count = sum(1 for h in self._health_status.values() if h.status == "healthy")
        unhealthy_count = sum(1 for h in self._health_status.values() if h.status == "unhealthy")
        unknown_count = sum(1 for h in self._health_status.values() if h.status == "unknown")

        return {
            "status": "healthy" if unhealthy_count == 0 else "degraded",
            "healthy_count": healthy_count,
            "unhealthy_count": unhealthy_count,
            "unknown_count": unknown_count,
            "total_count": len(self._health_status),
            "providers": {name: health.to_dict() for name, health in self._health_status.items()},
            "last_check": max((h.last_checked for h in self._health_status.values()), default=0),
        }

    def get_dashboard_data(self) -> dict[str, Any]:
        """
        Get dashboard-formatted data.

        Returns:
            Dict with dashboard display data
        """
        status = self.get_status()

        # Add history summary
        if self._health_history:
            recent_history = self._health_history[-20:]
            status["history"] = recent_history

        return status

    def format_report(self) -> str:
        """
        Format health status into human-readable report.

        Returns:
            Formatted text report
        """
        status = self.get_status()

        if status["status"] == "no_data":
            return "Provider Monitor: No health data available"

        lines = ["Provider Health Status", "=" * 40]

        for name, health in status["providers"].items():
            icon = "[OK]" if health["status"] == "healthy" else "[FAIL]" if health["status"] == "unhealthy" else "[?]"
            line = f"{icon} {name}: {health['message']}"
            if health["latency_ms"] > 0:
                line += f" ({health['latency_ms']:.0f}ms)"
            if health["consecutive_failures"] > 0:
                line += f" [failures: {health['consecutive_failures']}]"
            lines.append(line)

        lines.append("=" * 40)
        lines.append(f"Summary: {status['healthy_count']}/{status['total_count']} healthy")

        return "\n".join(lines)

    def get_unhealthy_providers(self) -> list[ProviderHealth]:
        """
        Get list of unhealthy providers.

        Returns:
            List of ProviderHealth for unhealthy providers
        """
        return [h for h in self._health_status.values() if h.status == "unhealthy"]

    def get_provider_health(self, provider_name: str) -> ProviderHealth | None:
        """
        Get health status for specific provider.

        Args:
            provider_name: Provider name

        Returns:
            ProviderHealth or None if not found
        """
        return self._health_status.get(provider_name)
