"""
Test DockerProvider — Container-Based Execution

Tests the DockerProvider that executes actions inside Docker containers
for isolation and sandboxing.

Note: These tests use mocks to avoid requiring Docker to be installed.
For real Docker testing, run with Docker available and set TEST_WITH_REAL_DOCKER=1.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.chathan.providers.docker_provider import DockerProvider


def test_docker_provider_initialization():
    """Test DockerProvider initialization."""
    print("\n[TEST 1] DockerProvider initialization")

    # Default initialization
    provider = DockerProvider()
    assert provider.name == "docker"
    assert provider.docker_image == "ubuntu:22.04"
    assert provider.timeout == 300
    print("  [PASS] Default initialization")

    # Custom configuration
    provider = DockerProvider(
        docker_image="python:3.11",
        timeout=600,
        auto_pull=False,
    )
    assert provider.docker_image == "python:3.11"
    assert provider.timeout == 600
    assert provider.auto_pull == False
    print("  [PASS] Custom initialization")


def test_action_to_command_mapping():
    """Test action to command mapping."""
    print("\n[TEST 2] Action to command mapping")

    provider = DockerProvider()

    # Git actions
    assert provider._action_to_command("git_status", {}) == "git status"
    assert provider._action_to_command("git_diff", {}) == "git diff"
    print("  [PASS] Git actions mapped correctly")

    # File operations
    assert provider._action_to_command("list_directory", {}) == "ls -la ."
    assert provider._action_to_command("list_directory", {"path": "/tmp"}) == "ls -la /tmp"
    print("  [PASS] File operations mapped correctly")

    # Command execution
    cmd = provider._action_to_command("execute_command", {"command": "echo hello"})
    assert cmd == "echo hello"
    print("  [PASS] Execute command mapped correctly")

    # Unknown action
    assert provider._action_to_command("unknown_action", {}) is None
    print("  [PASS] Unknown action returns None")


def test_execute_action_success():
    """Test successful action execution (mocked)."""
    print("\n[TEST 3] Execute action - success case (mocked)")

    provider = DockerProvider()

    # Mock the async execution
    async def mock_execute_async(action, params):
        return {
            "status": "success",
            "output": "total 4\ndrwxr-xr-x 2 root root 4096 Jan 1 00:00 .",
            "action": action,
            "provider": "docker",
            "exit_code": 0,
        }

    provider._execute_async = mock_execute_async

    result = provider.execute("list_directory", {"path": "/tmp"})

    assert result["status"] == "success"
    assert result["provider"] == "docker"
    assert result["exit_code"] == 0
    assert "drwx" in result["output"]
    print("  [PASS] Action executed successfully")


def test_execute_action_failure():
    """Test action execution failure (mocked)."""
    print("\n[TEST 4] Execute action - failure case (mocked)")

    provider = DockerProvider()

    # Mock failed execution
    async def mock_execute_async(action, params):
        return {
            "status": "error",
            "output": "sh: command not found",
            "action": action,
            "provider": "docker",
            "exit_code": 127,
        }

    provider._execute_async = mock_execute_async

    result = provider.execute("unknown_command", {})

    assert result["status"] == "error"
    assert result["exit_code"] == 127
    assert "not found" in result["output"]
    print("  [PASS] Action failure handled correctly")


def test_execute_timeout():
    """Test command timeout (mocked)."""
    print("\n[TEST 5] Execute action - timeout (mocked)")

    provider = DockerProvider(timeout=5)

    # Mock timeout
    async def mock_execute_async(action, params):
        import asyncio
        raise asyncio.TimeoutError()

    provider._execute_async = mock_execute_async

    result = provider.execute("long_running_command", {})

    assert result["status"] == "error"
    assert "error" in result["output"].lower() or "timeout" in result["output"].lower()
    print("  [PASS] Timeout handled correctly")


def test_health_check_docker_available():
    """Test health check when Docker is available (mocked)."""
    print("\n[TEST 6] Health check - Docker available (mocked)")

    provider = DockerProvider()

    # Mock successful health check
    async def mock_health_check_async():
        return {
            "status": "healthy",
            "provider": "docker",
            "docker_version": "24.0.7",
        }

    provider._health_check_async = mock_health_check_async

    result = provider.health_check()

    assert result["status"] == "healthy"
    assert "docker_version" in result
    print("  [PASS] Health check successful")


def test_health_check_docker_not_running():
    """Test health check when Docker daemon is not running (mocked)."""
    print("\n[TEST 7] Health check - Docker not running (mocked)")

    provider = DockerProvider()

    # Mock Docker daemon not running
    async def mock_health_check_async():
        return {
            "status": "unhealthy",
            "provider": "docker",
            "error": "Docker daemon not running: Cannot connect to the Docker daemon",
        }

    provider._health_check_async = mock_health_check_async

    result = provider.health_check()

    assert result["status"] == "unhealthy"
    assert "error" in result
    print("  [PASS] Docker not running detected")


def test_health_check_docker_not_installed():
    """Test health check when Docker is not installed (mocked)."""
    print("\n[TEST 8] Health check - Docker not installed (mocked)")

    provider = DockerProvider()

    # Mock Docker not found
    async def mock_health_check_async():
        return {
            "status": "unhealthy",
            "provider": "docker",
            "error": "Docker command not found - is Docker installed?",
        }

    provider._health_check_async = mock_health_check_async

    result = provider.health_check()

    assert result["status"] == "unhealthy"
    assert "not found" in result["error"] or "installed" in result["error"]
    print("  [PASS] Docker not installed detected")


def test_cancel_job():
    """Test job cancellation (mocked)."""
    print("\n[TEST 9] Cancel job (mocked)")

    provider = DockerProvider()

    # Mock successful cancellation
    async def mock_kill_container(container_name):
        pass  # Simulate successful kill

    provider._kill_container = mock_kill_container

    result = provider.cancel("job-123")

    assert result["status"] == "success"
    assert result["provider"] == "docker"
    print("  [PASS] Job cancellation successful")


def test_worker_integration():
    """Test that DockerProvider works with worker interface."""
    print("\n[TEST 10] Worker integration")

    provider = DockerProvider()

    # Mock execute to simulate worker usage
    async def mock_execute_async(action, params):
        return {
            "status": "success",
            "output": "Hello from Docker!",
            "action": action,
            "provider": "docker",
            "exit_code": 0,
        }

    provider._execute_async = mock_execute_async

    # Simulate worker calling execute with action and params
    result = provider.execute(action="execute_command", params={"command": "echo hello"})

    assert result["status"] == "success"
    assert result["action"] == "execute_command"
    assert isinstance(result["output"], str)
    assert isinstance(result["exit_code"], int)
    print("  [PASS] Worker integration works correctly")


def test_with_real_docker():
    """Test with real Docker if available and enabled."""
    print("\n[TEST 11] Real Docker execution (optional)")

    # Skip if not enabled
    if not os.environ.get("TEST_WITH_REAL_DOCKER"):
        print("  [SKIP] Set TEST_WITH_REAL_DOCKER=1 to run real Docker tests")
        return

    provider = DockerProvider(docker_image="alpine:latest", auto_pull=True)

    # Test health check
    health = provider.health_check()
    if health["status"] != "healthy":
        print(f"  [SKIP] Docker not available: {health.get('error', 'unknown')}")
        return

    print("  [INFO] Docker is available, running real test...")

    # Test simple command
    result = provider.execute("execute_command", {"command": "echo 'Hello from Docker!'"})

    assert result["status"] == "success"
    assert result["exit_code"] == 0
    assert "Hello from Docker" in result["output"]
    print("  [PASS] Real Docker execution successful")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("DockerProvider Tests")
    print("=" * 60)

    try:
        test_docker_provider_initialization()
        test_action_to_command_mapping()
        test_execute_action_success()
        test_execute_action_failure()
        test_execute_timeout()
        test_health_check_docker_available()
        test_health_check_docker_not_running()
        test_health_check_docker_not_installed()
        test_cancel_job()
        test_worker_integration()
        test_with_real_docker()

        print("\n" + "=" * 60)
        print("[SUCCESS] All DockerProvider tests passed!")
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


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
