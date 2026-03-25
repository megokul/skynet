from __future__ import annotations

from ssh_tunnel_health import SSHHealthTracker


def test_health_tracker_classifies_capacity_and_timeout_errors() -> None:
    tracker = SSHHealthTracker(circuit_breaker_seconds=30, capacity_backoff_seconds=11)

    assert tracker.classify_error("Exceeded MaxStartups") == "capacity"
    assert tracker.classify_error("connection timed out") == "timeout"
    assert tracker.retry_delay_for_category("capacity", 1) == 11


def test_health_tracker_opens_circuit_after_repeated_failures() -> None:
    tracker = SSHHealthTracker(circuit_breaker_seconds=30, capacity_backoff_seconds=11)

    tracker.record_failure("capacity")
    tracker.record_failure("capacity")

    diagnostics = tracker.diagnostics(
        configured=True,
        endpoint="host:22",
        healthy=False,
    )
    assert diagnostics["ssh_failure_streak"] == 2
    assert diagnostics["ssh_circuit_open_until"] > 0
    assert diagnostics["ssh_health_ok"] is False
