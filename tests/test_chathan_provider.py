"""
Test ChathanProvider — OpenClaw Gateway Integration

Tests the ChathanProvider that communicates with the OpenClaw Gateway
HTTP API to execute actions via the connected CHATHAN worker.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.chathan.providers.chathan_provider import ChathanProvider


def test_chathan_provider_initialization():
    """Test ChathanProvider initialization."""
    print("\n[TEST 1] ChathanProvider initialization")

    # Default URL
    provider = ChathanProvider()
    assert provider.name == "chathan"
    assert provider.gateway_api_url == "http://127.0.0.1:8766"
    print("  [PASS] Default initialization")

    # Custom URL
    provider = ChathanProvider(gateway_api_url="http://localhost:9000")
    assert provider.gateway_api_url == "http://localhost:9000"
    print("  [PASS] Custom URL initialization")


def test_execute_action_success():
    """Test successful action execution."""
    print("\n[TEST 2] Execute action - success case")

    provider = ChathanProvider()

    # Mock the async HTTP call to simulate successful gateway response
    mock_response_data = {
        "status": "success",
        "result": {
            "stdout": "On branch main\nnothing to commit, working tree clean",
            "stderr": "",
            "returncode": 0,
        }
    }

    async def mock_execute_async(action, params):
        return {
            "status": "success",
            "output": mock_response_data["result"]["stdout"],
            "action": action,
            "provider": "chathan",
            "exit_code": 0,
        }

    # Patch the async method
    provider._execute_async = mock_execute_async

    result = provider.execute("git_status", {"working_dir": "/tmp"})

    assert result["status"] == "success"
    assert result["provider"] == "chathan"
    assert result["action"] == "git_status"
    assert result["exit_code"] == 0
    assert "main" in result["output"]
    print("  [PASS] Action executed successfully")


def test_execute_action_failure():
    """Test action execution failure."""
    print("\n[TEST 3] Execute action - failure case")

    provider = ChathanProvider()

    # Mock the async HTTP call to simulate failed command
    async def mock_execute_async(action, params):
        return {
            "status": "error",
            "output": "fatal: not a git repository",
            "action": action,
            "provider": "chathan",
            "exit_code": 128,
        }

    provider._execute_async = mock_execute_async

    result = provider.execute("git_status", {"working_dir": "/tmp"})

    assert result["status"] == "error"
    assert result["exit_code"] == 128
    assert "not a git repository" in result["output"]
    print("  [PASS] Action failure handled correctly")


def test_execute_gateway_unreachable():
    """Test behavior when gateway is unreachable."""
    print("\n[TEST 4] Execute action - gateway unreachable")

    provider = ChathanProvider()

    # Mock the async method to raise connection error
    async def mock_execute_async(action, params):
        import aiohttp
        raise aiohttp.ClientError("Connection refused")

    provider._execute_async = mock_execute_async

    result = provider.execute("git_status", {"working_dir": "/tmp"})

    assert result["status"] == "error"
    assert "error" in result["output"].lower() or "unreachable" in result["output"].lower()
    assert result["exit_code"] == -1
    print("  [PASS] Gateway unreachable handled correctly")


def test_health_check_agent_connected():
    """Test health check when agent is connected."""
    print("\n[TEST 5] Health check - agent connected")

    provider = ChathanProvider()

    # Mock successful health check
    async def mock_health_check_async():
        return {
            "status": "healthy",
            "provider": "chathan",
            "agent_connected": True,
        }

    provider._health_check_async = mock_health_check_async

    result = provider.health_check()

    assert result["status"] == "healthy"
    assert result["agent_connected"] is True
    print("  [PASS] Health check successful")


def test_health_check_no_agent():
    """Test health check when no agent is connected."""
    print("\n[TEST 6] Health check - no agent connected")

    provider = ChathanProvider()

    # Mock degraded health check
    async def mock_health_check_async():
        return {
            "status": "degraded",
            "provider": "chathan",
            "agent_connected": False,
            "message": "Gateway online but no agent connected",
        }

    provider._health_check_async = mock_health_check_async

    result = provider.health_check()

    assert result["status"] == "degraded"
    assert result["agent_connected"] is False
    print("  [PASS] No agent detected correctly")


def test_health_check_gateway_unreachable():
    """Test health check when gateway is unreachable."""
    print("\n[TEST 7] Health check - gateway unreachable")

    provider = ChathanProvider()

    # Mock failed health check
    async def mock_health_check_async():
        import aiohttp
        raise aiohttp.ClientError("Connection refused")

    provider._health_check_async = mock_health_check_async

    result = provider.health_check()

    assert result["status"] == "unhealthy"
    assert "error" in result
    print("  [PASS] Gateway unreachable detected")


def test_cancel_job():
    """Test job cancellation."""
    print("\n[TEST 8] Cancel job")

    provider = ChathanProvider()

    # Mock successful cancellation
    async def mock_cancel_async(job_id):
        return {
            "status": "success",
            "provider": "chathan",
            "message": f"Emergency stop sent for job {job_id}",
        }

    provider._cancel_async = mock_cancel_async

    result = provider.cancel("job-123")

    assert result["status"] == "success"
    assert "job-123" in result["message"]
    print("  [PASS] Job cancellation successful")


def test_cancel_job_failure():
    """Test job cancellation failure."""
    print("\n[TEST 9] Cancel job - failure case")

    provider = ChathanProvider()

    # Mock failed cancellation
    async def mock_cancel_async(job_id):
        import aiohttp
        raise aiohttp.ClientError("Gateway unreachable")

    provider._cancel_async = mock_cancel_async

    result = provider.cancel("job-123")

    assert result["status"] == "error"
    assert "error" in result
    print("  [PASS] Cancellation failure handled")


def test_worker_integration():
    """Test that ChathanProvider works with worker interface."""
    print("\n[TEST 10] Worker integration")

    provider = ChathanProvider()

    # Mock execute to simulate worker usage
    async def mock_execute_async(action, params):
        return {
            "status": "success",
            "output": "Command executed successfully",
            "action": action,
            "provider": "chathan",
            "exit_code": 0,
        }

    provider._execute_async = mock_execute_async

    # Simulate worker calling execute with action and params
    result = provider.execute(action="list_directory", params={"working_dir": "/tmp"})

    assert result["status"] == "success"
    assert result["action"] == "list_directory"
    assert isinstance(result["output"], str)
    assert isinstance(result["exit_code"], int)
    print("  [PASS] Worker integration works correctly")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("ChathanProvider Tests")
    print("=" * 60)

    try:
        test_chathan_provider_initialization()
        test_execute_action_success()
        test_execute_action_failure()
        test_execute_gateway_unreachable()
        test_health_check_agent_connected()
        test_health_check_no_agent()
        test_health_check_gateway_unreachable()
        test_cancel_job()
        test_cancel_job_failure()
        test_worker_integration()

        print("\n" + "=" * 60)
        print("[SUCCESS] All ChathanProvider tests passed!")
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
