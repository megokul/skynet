"""
Test ProviderMonitor Integration — Real Provider Health Monitoring

Tests ProviderMonitor with actual execution providers from the worker.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.chathan.providers.mock_provider import MockProvider
from skynet.chathan.providers.local_provider import LocalProvider
from skynet.sentinel.provider_monitor import ProviderMonitor


async def test_monitor_with_real_providers():
    """Test provider monitor with real providers."""
    print("\n" + "=" * 60)
    print("Provider Monitor Integration Test")
    print("=" * 60)

    # Create real providers
    providers = {
        "mock": MockProvider(),
        "local": LocalProvider(allowed_paths=[str(Path.cwd())]),
    }

    print(f"\n[INFO] Testing with {len(providers)} providers:")
    for name in providers:
        print(f"  - {name}")

    # Create monitor
    monitor = ProviderMonitor(providers=providers, check_interval=30)

    # Run single health check
    print("\n[TEST] Running health check on all providers...")
    result = await monitor.check_all_providers()

    print(f"\n[RESULT] Checked {len(result)} providers")
    for name, health in result.items():
        status_icon = "[OK]" if health.status == "healthy" else "[FAIL]"
        print(f"  {status_icon} {name}: {health.message} ({health.latency_ms:.1f}ms)")

    # Get status
    status = monitor.get_status()
    print(f"\n[STATUS] Overall: {status['status']}")
    print(f"  Healthy: {status['healthy_count']}/{status['total_count']}")
    print(f"  Unhealthy: {status['unhealthy_count']}/{status['total_count']}")

    # Generate report
    print("\n[REPORT]")
    print(monitor.format_report())

    # Test background monitoring
    print("\n[TEST] Starting background monitoring for 5 seconds...")
    monitor.start()
    await asyncio.sleep(5)
    await monitor.stop()

    print(f"\n[INFO] Health checks performed: {len(monitor._health_history)}")

    # Get dashboard data
    dashboard = monitor.get_dashboard_data()
    print(f"\n[DASHBOARD] Status: {dashboard['status']}")
    print(f"  Total providers: {dashboard['total_count']}")
    print(f"  History entries: {len(dashboard.get('history', []))}")

    print("\n" + "=" * 60)
    print("[SUCCESS] Integration test completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_monitor_with_real_providers())
