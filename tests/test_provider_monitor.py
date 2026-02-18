"""
Test ProviderMonitor — Provider Health Monitoring

Tests the ProviderMonitor that tracks health of all execution providers.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.sentinel.provider_monitor import ProviderMonitor, ProviderHealth


class MockHealthyProvider:
    """Mock provider that always reports healthy."""

    def __init__(self, name: str = "mock"):
        self.name = name

    def health_check(self) -> dict[str, str]:
        return {"status": "healthy", "provider": self.name}


class MockUnhealthyProvider:
    """Mock provider that always reports unhealthy."""

    def __init__(self, name: str = "mock", error_msg: str = "Service unavailable"):
        self.name = name
        self.error_msg = error_msg

    def health_check(self) -> dict[str, str]:
        return {"status": "unhealthy", "provider": self.name, "error": self.error_msg}


class MockSlowProvider:
    """Mock provider with slow health check."""

    def __init__(self, name: str = "mock", delay: float = 0.1):
        self.name = name
        self.delay = delay

    async def health_check(self) -> dict[str, str]:
        await asyncio.sleep(self.delay)
        return {"status": "healthy", "provider": self.name}


class MockNoHealthCheckProvider:
    """Mock provider without health_check method."""

    def __init__(self, name: str = "mock"):
        self.name = name


def test_provider_monitor_initialization():
    """Test ProviderMonitor initialization."""
    print("\n[TEST 1] ProviderMonitor initialization")

    providers = {
        "mock1": MockHealthyProvider("mock1"),
        "mock2": MockHealthyProvider("mock2"),
    }

    monitor = ProviderMonitor(providers=providers, check_interval=60)
    assert monitor.providers == providers
    assert monitor.check_interval == 60
    assert monitor._health_status == {}
    assert monitor._running == False
    print("  [PASS] Monitor initialized correctly")


async def test_check_single_healthy_provider():
    """Test checking a single healthy provider."""
    print("\n[TEST 2] Check single healthy provider")

    provider = MockHealthyProvider("test")
    monitor = ProviderMonitor(providers={"test": provider})

    health = await monitor.check_provider("test", provider)

    assert health.provider_name == "test"
    assert health.status == "healthy"
    assert health.message == "OK"
    assert health.consecutive_failures == 0
    assert health.latency_ms >= 0
    print("  [PASS] Healthy provider check")


async def test_check_single_unhealthy_provider():
    """Test checking a single unhealthy provider."""
    print("\n[TEST 3] Check single unhealthy provider")

    provider = MockUnhealthyProvider("test", "Connection refused")
    monitor = ProviderMonitor(providers={"test": provider})

    health = await monitor.check_provider("test", provider)

    assert health.provider_name == "test"
    assert health.status == "unhealthy"
    assert "Connection refused" in health.message
    assert health.consecutive_failures == 1
    print("  [PASS] Unhealthy provider check")


async def test_check_all_providers():
    """Test checking all providers."""
    print("\n[TEST 4] Check all providers")

    providers = {
        "healthy1": MockHealthyProvider("healthy1"),
        "healthy2": MockHealthyProvider("healthy2"),
        "unhealthy": MockUnhealthyProvider("unhealthy", "Service down"),
    }

    monitor = ProviderMonitor(providers=providers)
    result = await monitor.check_all_providers()

    assert len(result) == 3
    assert result["healthy1"].status == "healthy"
    assert result["healthy2"].status == "healthy"
    assert result["unhealthy"].status == "unhealthy"
    print("  [PASS] All providers checked")


async def test_consecutive_failures():
    """Test consecutive failure counting."""
    print("\n[TEST 5] Consecutive failure counting")

    provider = MockUnhealthyProvider("test", "Error")
    monitor = ProviderMonitor(providers={"test": provider})

    # First check
    health1 = await monitor.check_provider("test", provider)
    assert health1.consecutive_failures == 1
    monitor._health_status["test"] = health1

    # Second check (should increment)
    health2 = await monitor.check_provider("test", provider)
    assert health2.consecutive_failures == 2
    monitor._health_status["test"] = health2

    # Third check
    health3 = await monitor.check_provider("test", provider)
    assert health3.consecutive_failures == 3
    print("  [PASS] Consecutive failures counted correctly")


async def test_failure_recovery():
    """Test failure count reset on recovery."""
    print("\n[TEST 6] Failure recovery")

    # Start with unhealthy provider
    unhealthy_provider = MockUnhealthyProvider("test", "Error")
    monitor = ProviderMonitor(providers={"test": unhealthy_provider})

    # Fail twice
    health1 = await monitor.check_provider("test", unhealthy_provider)
    monitor._health_status["test"] = health1
    health2 = await monitor.check_provider("test", unhealthy_provider)
    monitor._health_status["test"] = health2
    assert health2.consecutive_failures == 2

    # Switch to healthy provider
    healthy_provider = MockHealthyProvider("test")
    health3 = await monitor.check_provider("test", healthy_provider)
    assert health3.status == "healthy"
    assert health3.consecutive_failures == 0
    print("  [PASS] Failure count reset on recovery")


async def test_get_status():
    """Test get_status method."""
    print("\n[TEST 7] Get status")

    providers = {
        "healthy": MockHealthyProvider("healthy"),
        "unhealthy": MockUnhealthyProvider("unhealthy"),
    }

    monitor = ProviderMonitor(providers=providers)
    await monitor.check_all_providers()

    status = monitor.get_status()

    assert status["status"] == "degraded"  # Has unhealthy provider
    assert status["healthy_count"] == 1
    assert status["unhealthy_count"] == 1
    assert status["total_count"] == 2
    assert "providers" in status
    print("  [PASS] Status retrieved correctly")


async def test_get_unhealthy_providers():
    """Test get_unhealthy_providers method."""
    print("\n[TEST 8] Get unhealthy providers")

    providers = {
        "healthy1": MockHealthyProvider("healthy1"),
        "unhealthy1": MockUnhealthyProvider("unhealthy1"),
        "unhealthy2": MockUnhealthyProvider("unhealthy2"),
    }

    monitor = ProviderMonitor(providers=providers)
    await monitor.check_all_providers()

    unhealthy = monitor.get_unhealthy_providers()

    assert len(unhealthy) == 2
    assert all(h.status == "unhealthy" for h in unhealthy)
    print("  [PASS] Unhealthy providers identified")


async def test_format_report():
    """Test format_report method."""
    print("\n[TEST 9] Format report")

    providers = {
        "healthy": MockHealthyProvider("healthy"),
        "unhealthy": MockUnhealthyProvider("unhealthy", "Connection failed"),
    }

    monitor = ProviderMonitor(providers=providers)
    await monitor.check_all_providers()

    report = monitor.format_report()

    assert "Provider Health Status" in report
    assert "healthy" in report
    assert "unhealthy" in report
    assert "1/2 healthy" in report
    print("  [PASS] Report formatted correctly")


async def test_health_history():
    """Test health history tracking."""
    print("\n[TEST 10] Health history tracking")

    provider = MockHealthyProvider("test")
    monitor = ProviderMonitor(providers={"test": provider})

    # Perform multiple checks
    await monitor.check_all_providers()
    await asyncio.sleep(0.01)
    await monitor.check_all_providers()
    await asyncio.sleep(0.01)
    await monitor.check_all_providers()

    assert len(monitor._health_history) == 3
    assert all("timestamp" in h for h in monitor._health_history)
    assert all("providers" in h for h in monitor._health_history)
    print("  [PASS] Health history tracked")


async def test_latency_measurement():
    """Test latency measurement for health checks."""
    print("\n[TEST 11] Latency measurement")

    provider = MockSlowProvider("slow", delay=0.1)
    monitor = ProviderMonitor(providers={"slow": provider})

    health = await monitor.check_provider("slow", provider)

    assert health.latency_ms >= 100  # At least 100ms due to 0.1s delay
    print(f"  [INFO] Measured latency: {health.latency_ms:.1f}ms")
    print("  [PASS] Latency measured correctly")


async def test_provider_without_health_check():
    """Test provider without health_check method."""
    print("\n[TEST 12] Provider without health_check")

    provider = MockNoHealthCheckProvider("no-health")
    monitor = ProviderMonitor(providers={"no-health": provider})

    health = await monitor.check_provider("no-health", provider)

    assert health.status == "unknown"
    assert "does not implement" in health.message
    print("  [PASS] Missing health_check handled")


async def test_dashboard_data():
    """Test dashboard data generation."""
    print("\n[TEST 13] Dashboard data")

    providers = {
        "healthy": MockHealthyProvider("healthy"),
        "unhealthy": MockUnhealthyProvider("unhealthy"),
    }

    monitor = ProviderMonitor(providers=providers)
    await monitor.check_all_providers()

    dashboard = monitor.get_dashboard_data()

    assert "status" in dashboard
    assert "providers" in dashboard
    assert "healthy_count" in dashboard
    assert "history" in dashboard
    print("  [PASS] Dashboard data generated")


async def test_background_monitoring():
    """Test background monitoring loop."""
    print("\n[TEST 14] Background monitoring loop")

    provider = MockHealthyProvider("test")
    monitor = ProviderMonitor(providers={"test": provider}, check_interval=0.1)

    # Start monitoring
    monitor.start()
    assert monitor._running == True
    assert monitor._task is not None

    # Wait for a few checks
    await asyncio.sleep(0.25)

    # Should have performed multiple checks
    assert len(monitor._health_history) >= 2

    # Stop monitoring
    await monitor.stop()
    assert monitor._running == False

    print("  [PASS] Background monitoring works")


async def test_get_provider_health():
    """Test get_provider_health method."""
    print("\n[TEST 15] Get provider health")

    providers = {
        "test1": MockHealthyProvider("test1"),
        "test2": MockUnhealthyProvider("test2"),
    }

    monitor = ProviderMonitor(providers=providers)
    await monitor.check_all_providers()

    health1 = monitor.get_provider_health("test1")
    assert health1 is not None
    assert health1.status == "healthy"

    health2 = monitor.get_provider_health("test2")
    assert health2 is not None
    assert health2.status == "unhealthy"

    health_none = monitor.get_provider_health("nonexistent")
    assert health_none is None

    print("  [PASS] Provider health retrieved")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("ProviderMonitor Tests")
    print("=" * 60)

    async def run_async_tests():
        try:
            test_provider_monitor_initialization()
            await test_check_single_healthy_provider()
            await test_check_single_unhealthy_provider()
            await test_check_all_providers()
            await test_consecutive_failures()
            await test_failure_recovery()
            await test_get_status()
            await test_get_unhealthy_providers()
            await test_format_report()
            await test_health_history()
            await test_latency_measurement()
            await test_provider_without_health_check()
            await test_dashboard_data()
            await test_background_monitoring()
            await test_get_provider_health()

            print("\n" + "=" * 60)
            print("[SUCCESS] All ProviderMonitor tests passed!")
            print("=" * 60)

            return True

        except AssertionError as e:
            print(f"\n[FAILED] Test assertion failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        except Exception as e:
            print(f"\n[ERROR] Test error: {e}")
            import traceback
            traceback.print_exc()
            return False

    return asyncio.run(run_async_tests())


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
