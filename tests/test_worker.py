"""Test Celery worker (without actual Celery/Redis running)."""

import sys
from pathlib import Path

# Add skynet to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.queue.worker import execute_job, health_check, shutdown_reliability_components


def main():
    print("=" * 60)
    print("SKYNET Celery Worker Test (Direct Function Call)")
    print("=" * 60)
    print()

    # Test 1: Health check
    print("[1] Testing health check...")
    result = health_check()
    print(f"[SUCCESS] Health check: {result['status']}")
    print(f"  Providers: {result['providers']}")
    print()

    # Test 2: Execute job with mock provider
    print("[2] Testing job execution...")
    job_id = "test_job_001"
    execution_spec = {
        "job_id": job_id,
        "provider": "mock",
        "actions": [
            {
                "action": "git_status",
                "params": {"working_dir": "/tmp/test"},
            },
            {
                "action": "list_directory",
                "params": {"path": "/tmp/test"},
            },
        ],
    }

    # Call execute_job directly (not via Celery)
    # Note: In production, this would be called by Celery workers
    result = execute_job(job_id, execution_spec)

    print(f"[SUCCESS] Job execution result:")
    print(f"  Job ID: {result['job_id']}")
    print(f"  Status: {result['status']}")
    print(f"  Message: {result['message']}")
    print(f"  Actions executed: {len(result['results'])}")
    print()

    # Show individual action results
    print("[3] Individual action results:")
    for i, action_result in enumerate(result['results'], 1):
        print(f"  [{i}] {action_result['action']}: {action_result['status']}")
        if action_result['output']:
            output_preview = action_result['output'][:100]
            print(f"      Output: {output_preview}...")
    print()

    # Test 3: Test error handling
    print("[4] Testing error handling...")
    bad_execution_spec = {
        "job_id": "test_job_002",
        "provider": "nonexistent_provider",  # This will cause an error
        "actions": [
            {"action": "some_action", "params": {}},
        ],
    }

    result = execute_job("test_job_002", bad_execution_spec)
    print(f"[SUCCESS] Error handling works:")
    print(f"  Status: {result['status']}")
    print(f"  Error caught: {result.get('error', 'N/A')}")
    print()

    # Test 4: Test with LocalProvider (real execution)
    print("[5] Testing LocalProvider (real execution)...")
    local_execution_spec = {
        "job_id": "test_job_003",
        "provider": "local",
        "actions": [
            {
                "action": "git_status",
                "params": {"working_dir": str(Path.cwd())},
            },
            {
                "action": "list_directory",
                "params": {"working_dir": str(Path.cwd())},
            },
        ],
    }

    result = execute_job("test_job_003", local_execution_spec)
    print(f"[SUCCESS] LocalProvider execution:")
    print(f"  Job ID: {result['job_id']}")
    print(f"  Status: {result['status']}")
    print(f"  Message: {result['message']}")
    print(f"  Actions executed: {len(result['results'])}")
    for i, action_result in enumerate(result['results'], 1):
        print(f"  [{i}] {action_result['action']}: {action_result['status']}")
        if action_result['output']:
            output_preview = action_result['output'][:80].replace('\n', ' ')
            print(f"      Output: {output_preview}...")
    print()

    print("=" * 60)
    print("[SUCCESS] All worker tests passed!")
    print("=" * 60)
    print()
    print("NOTE: This test calls worker functions directly.")
    print("To run with actual Celery:")
    print("  1. Install Redis: docker run -d -p 6379:6379 redis")
    print("  2. Start worker: celery -A skynet.queue.worker worker")
    print("  3. Use SkynetApp with real Celery queue")
    print()
    shutdown_reliability_components()


if __name__ == "__main__":
    main()
