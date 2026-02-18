"""
Test LogStore — Execution Log Storage and Querying

Tests the LogStore that manages execution logs.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.archive.log_store import LogStore, LogEntry


async def test_log_store_initialization():
    """Test LogStore initialization."""
    print("\n[TEST 1] LogStore initialization")

    store = LogStore(log_storage_path="data/test_logs")
    assert store.log_storage_path.exists()
    assert store._recent_logs == []
    print("  [PASS] Store initialized")


async def test_log_entry():
    """Test logging an entry."""
    print("\n[TEST 2] Log entry")

    store = LogStore(log_storage_path="data/test_logs")

    entry = await store.log(
        job_id="job-123",
        level="info",
        message="Test message",
        metadata={"test": True},
    )

    assert entry.job_id == "job-123"
    assert entry.level == "info"
    assert entry.message == "Test message"
    assert entry.metadata["test"] == True
    print("  [PASS] Entry logged")


async def test_get_job_logs():
    """Test retrieving job logs."""
    print("\n[TEST 3] Get job logs")

    store = LogStore(log_storage_path="data/test_logs")

    # Log multiple entries
    await store.log("job-456", "info", "Starting job")
    await store.log("job-456", "debug", "Processing step 1")
    await store.log("job-456", "info", "Job completed")

    # Retrieve all logs for job
    logs = await store.get_job_logs("job-456")
    assert len(logs) >= 3
    assert all(log.job_id == "job-456" for log in logs)
    print("  [PASS] Job logs retrieved")


async def test_filter_logs_by_level():
    """Test filtering logs by level."""
    print("\n[TEST 4] Filter logs by level")

    store = LogStore(log_storage_path="data/test_logs")

    # Log entries with different levels
    await store.log("job-789", "info", "Info message")
    await store.log("job-789", "warning", "Warning message")
    await store.log("job-789", "error", "Error message")

    # Filter by level
    warnings = await store.get_job_logs("job-789", level="warning")
    assert all(log.level == "warning" for log in warnings)

    errors = await store.get_job_logs("job-789", level="error")
    assert all(log.level == "error" for log in errors)
    print("  [PASS] Logs filtered by level")


async def test_tail_logs():
    """Test tailing logs."""
    print("\n[TEST 5] Tail logs")

    store = LogStore(log_storage_path="data/test_logs")

    # Log many entries
    for i in range(100):
        await store.log("job-tail", "info", f"Message {i}")

    # Tail last 10 entries
    tail = await store.tail_job_logs("job-tail", num_lines=10)
    assert len(tail) == 10
    # Should be the last 10 entries
    assert "Message 99" in tail[-1].message
    print("  [PASS] Logs tailed")


async def test_search_logs():
    """Test searching logs."""
    print("\n[TEST 6] Search logs")

    store = LogStore(log_storage_path="data/test_logs")

    # Log searchable entries
    await store.log("job-search", "info", "Processing user request")
    await store.log("job-search", "info", "Fetching data from database")
    await store.log("job-search", "info", "User request completed")

    # Search for "user"
    results = await store.search_logs("user", job_id="job-search")
    assert len(results) >= 2
    assert all("user" in log.message.lower() for log in results)
    print("  [PASS] Logs searched")


async def test_recent_logs():
    """Test getting recent logs."""
    print("\n[TEST 7] Get recent logs")

    store = LogStore(log_storage_path="data/test_logs")

    # Log some entries
    await store.log("job-recent-1", "info", "Message 1")
    await store.log("job-recent-2", "error", "Error message")
    await store.log("job-recent-3", "info", "Message 3")

    # Get recent logs
    recent = await store.get_recent_logs(limit=5)
    assert len(recent) >= 3

    # Get recent errors only
    recent_errors = await store.get_recent_logs(level="error", limit=5)
    assert all(log.level == "error" for log in recent_errors)
    print("  [PASS] Recent logs retrieved")


async def test_delete_job_logs():
    """Test deleting job logs."""
    print("\n[TEST 8] Delete job logs")

    store = LogStore(log_storage_path="data/test_logs")

    # Log entries
    await store.log("job-delete", "info", "Entry 1")
    await store.log("job-delete", "info", "Entry 2")

    # Verify log file exists
    log_file = store._get_log_file_path("job-delete")
    assert log_file.exists()

    # Delete logs
    result = await store.delete_job_logs("job-delete")
    assert result == True
    assert not log_file.exists()

    # Verify logs are gone
    logs = await store.get_job_logs("job-delete")
    assert len(logs) == 0
    print("  [PASS] Job logs deleted")


async def test_log_stats():
    """Test log statistics."""
    print("\n[TEST 9] Log statistics")

    store = LogStore(log_storage_path="data/test_logs")

    # Log some entries
    await store.log("job-stats", "info", "Info message")
    await store.log("job-stats", "error", "Error message")
    await store.log("job-stats", "warning", "Warning message")

    # Get stats
    stats = await store.get_log_stats()

    assert "total_log_files" in stats
    assert "recent_logs_count" in stats
    assert "recent_by_level" in stats
    assert stats["recent_logs_count"] >= 3
    print("  [PASS] Log stats retrieved")


async def test_format_logs():
    """Test formatting logs for display."""
    print("\n[TEST 10] Format logs")

    store = LogStore(log_storage_path="data/test_logs")

    # Log entries
    await store.log("job-format", "info", "Message 1", metadata={"key": "value"})
    await store.log("job-format", "error", "Message 2")

    # Get logs
    logs = await store.get_job_logs("job-format")

    # Format without metadata
    formatted = await store.format_logs(logs, include_metadata=False)
    assert "Message 1" in formatted
    assert "Message 2" in formatted
    assert "key" not in formatted

    # Format with metadata
    formatted_meta = await store.format_logs(logs, include_metadata=True)
    assert "key" in formatted_meta
    assert "value" in formatted_meta
    print("  [PASS] Logs formatted")


async def test_log_entry_serialization():
    """Test LogEntry JSON serialization."""
    print("\n[TEST 11] LogEntry serialization")

    entry = LogEntry(
        job_id="job-serialize",
        timestamp=1234567890.0,
        level="info",
        message="Test message",
        metadata={"key": "value"},
    )

    # Serialize
    json_str = entry.to_json()
    assert "job-serialize" in json_str
    assert "Test message" in json_str

    # Deserialize
    deserialized = LogEntry.from_json(json_str)
    assert deserialized.job_id == entry.job_id
    assert deserialized.timestamp == entry.timestamp
    assert deserialized.level == entry.level
    assert deserialized.message == entry.message
    assert deserialized.metadata["key"] == "value"
    print("  [PASS] Serialization works")


async def test_cleanup_old_logs():
    """Test cleanup of old logs."""
    print("\n[TEST 12] Cleanup old logs")

    store = LogStore(log_storage_path="data/test_logs")

    # Create a log file and manually modify its timestamp
    await store.log("job-old", "info", "Old message")

    log_file = store._get_log_file_path("job-old")
    assert log_file.exists()

    # Make file appear old by modifying its timestamp
    import os
    import time
    old_time = time.time() - (31 * 24 * 60 * 60)  # 31 days ago
    os.utime(log_file, (old_time, old_time))

    # Cleanup logs older than 30 days
    count = await store.cleanup_old_logs(max_age_days=30)
    assert count >= 1

    # Verify it's gone
    assert not log_file.exists()
    print("  [PASS] Old logs cleaned up")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("LogStore Tests")
    print("=" * 60)

    async def run_async_tests():
        try:
            await test_log_store_initialization()
            await test_log_entry()
            await test_get_job_logs()
            await test_filter_logs_by_level()
            await test_tail_logs()
            await test_search_logs()
            await test_recent_logs()
            await test_delete_job_logs()
            await test_log_stats()
            await test_format_logs()
            await test_log_entry_serialization()
            await test_cleanup_old_logs()

            print("\n" + "=" * 60)
            print("[SUCCESS] All LogStore tests passed!")
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
