"""
Test SSHProvider — Remote Execution via SSH

Tests the SSHProvider that executes actions on remote machines via SSH.

Note: These tests use mocks to avoid requiring SSH access.
For real SSH testing, run with SSH_TEST_HOST set and TEST_WITH_REAL_SSH=1.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.chathan.providers.ssh_provider import SSHProvider


def test_ssh_provider_initialization():
    """Test SSHProvider initialization."""
    print("\n[TEST 1] SSHProvider initialization")

    # Default initialization
    provider = SSHProvider()
    assert provider.name == "ssh"
    assert provider.host == "localhost"
    assert provider.port == 22
    assert provider.username == "ubuntu"
    assert provider.timeout == 120
    print("  [PASS] Default initialization")

    # Custom configuration
    provider = SSHProvider(
        host="192.168.1.100",
        port=2222,
        username="admin",
        key_path="/path/to/key",
        timeout=300,
    )
    assert provider.host == "192.168.1.100"
    assert provider.port == 2222
    assert provider.username == "admin"
    assert provider.key_path == "/path/to/key"
    assert provider.timeout == 300
    print("  [PASS] Custom initialization")


def test_action_to_command_mapping():
    """Test action to command mapping."""
    print("\n[TEST 2] Action to command mapping")

    provider = SSHProvider()

    # Git actions
    assert provider._action_to_command("git_status", {}) == "git status"
    assert provider._action_to_command("git_diff", {}) == "git diff"
    assert provider._action_to_command("git_pull", {}) == "git pull"
    print("  [PASS] Git actions mapped correctly")

    # File operations
    assert provider._action_to_command("list_directory", {}) == "ls -la ."
    assert provider._action_to_command("list_directory", {"path": "/var/log"}) == "ls -la /var/log"
    print("  [PASS] File operations mapped correctly")

    # System actions
    assert provider._action_to_command("check_disk_space", {}) == "df -h"
    assert provider._action_to_command("check_memory", {}) == "free -h"
    print("  [PASS] System actions mapped correctly")

    # Command execution
    cmd = provider._action_to_command("execute_command", {"command": "uptime"})
    assert cmd == "uptime"
    print("  [PASS] Execute command mapped correctly")

    # Unknown action
    assert provider._action_to_command("unknown_action", {}) is None
    print("  [PASS] Unknown action returns None")


def test_build_ssh_command():
    """Test SSH command building."""
    print("\n[TEST 3] Build SSH command")

    provider = SSHProvider(host="example.com", username="user", port=22)
    cmd = provider._build_ssh_command("ls -la", "/home/user")

    assert cmd[0] == "ssh"
    assert "user@example.com" in cmd
    assert "cd /home/user && ls -la" in cmd
    assert "-o" in cmd  # Options present
    print("  [PASS] SSH command built correctly")

    # With custom port
    provider = SSHProvider(host="example.com", username="user", port=2222)
    cmd = provider._build_ssh_command("echo test", "/tmp")
    assert "-p" in cmd
    assert "2222" in cmd
    print("  [PASS] Custom port handled correctly")

    # With key path
    provider = SSHProvider(host="example.com", username="user", key_path="/path/to/key")
    cmd = provider._build_ssh_command("pwd", "/tmp")
    assert "-i" in cmd
    assert "/path/to/key" in cmd
    print("  [PASS] Key path handled correctly")


def test_execute_action_success():
    """Test successful action execution (mocked)."""
    print("\n[TEST 4] Execute action - success case (mocked)")

    provider = SSHProvider()

    # Mock the async execution
    async def mock_execute_async(action, params):
        return {
            "status": "success",
            "output": "total 12K\ndrwxr-xr-x 3 user user 4.0K Jan 1 00:00 .",
            "action": action,
            "provider": "ssh",
            "exit_code": 0,
        }

    provider._execute_async = mock_execute_async

    result = provider.execute("list_directory", {"path": "/home/user"})

    assert result["status"] == "success"
    assert result["provider"] == "ssh"
    assert result["exit_code"] == 0
    assert "drwx" in result["output"]
    print("  [PASS] Action executed successfully")


def test_execute_action_failure():
    """Test action execution failure (mocked)."""
    print("\n[TEST 5] Execute action - failure case (mocked)")

    provider = SSHProvider()

    # Mock failed execution
    async def mock_execute_async(action, params):
        return {
            "status": "error",
            "output": "Permission denied",
            "action": action,
            "provider": "ssh",
            "exit_code": 1,
        }

    provider._execute_async = mock_execute_async

    result = provider.execute("list_directory", {"path": "/root"})

    assert result["status"] == "error"
    assert result["exit_code"] == 1
    assert "denied" in result["output"].lower()
    print("  [PASS] Action failure handled correctly")


def test_execute_timeout():
    """Test command timeout (mocked)."""
    print("\n[TEST 6] Execute action - timeout (mocked)")

    provider = SSHProvider(timeout=5)

    # Mock timeout
    async def mock_execute_async(action, params):
        import asyncio
        raise asyncio.TimeoutError()

    provider._execute_async = mock_execute_async

    result = provider.execute("long_running_command", {})

    assert result["status"] == "error"
    assert "error" in result["output"].lower() or "timeout" in result["output"].lower()
    print("  [PASS] Timeout handled correctly")


def test_health_check_ssh_available():
    """Test health check when SSH is available (mocked)."""
    print("\n[TEST 7] Health check - SSH available (mocked)")

    provider = SSHProvider()

    # Mock successful health check
    async def mock_health_check_async():
        return {
            "status": "healthy",
            "provider": "ssh",
            "host": "ubuntu@localhost:22",
        }

    provider._health_check_async = mock_health_check_async

    result = provider.health_check()

    assert result["status"] == "healthy"
    assert "host" in result
    print("  [PASS] Health check successful")


def test_health_check_ssh_connection_failed():
    """Test health check when SSH connection fails (mocked)."""
    print("\n[TEST 8] Health check - connection failed (mocked)")

    provider = SSHProvider()

    # Mock connection failure
    async def mock_health_check_async():
        return {
            "status": "unhealthy",
            "provider": "ssh",
            "error": "SSH connection failed: Connection refused",
        }

    provider._health_check_async = mock_health_check_async

    result = provider.health_check()

    assert result["status"] == "unhealthy"
    assert "error" in result
    print("  [PASS] Connection failure detected")


def test_health_check_ssh_not_installed():
    """Test health check when SSH is not installed (mocked)."""
    print("\n[TEST 9] Health check - SSH not installed (mocked)")

    provider = SSHProvider()

    # Mock SSH not found
    async def mock_health_check_async():
        return {
            "status": "unhealthy",
            "provider": "ssh",
            "error": "SSH command not found - is OpenSSH installed?",
        }

    provider._health_check_async = mock_health_check_async

    result = provider.health_check()

    assert result["status"] == "unhealthy"
    assert "not found" in result["error"] or "installed" in result["error"]
    print("  [PASS] SSH not installed detected")


def test_cancel_job():
    """Test job cancellation (not supported)."""
    print("\n[TEST 10] Cancel job (not supported)")

    provider = SSHProvider()

    result = provider.cancel("job-123")

    assert result["status"] == "not_supported"
    assert result["provider"] == "ssh"
    print("  [PASS] Cancellation returns not_supported")


def test_worker_integration():
    """Test that SSHProvider works with worker interface."""
    print("\n[TEST 11] Worker integration")

    provider = SSHProvider()

    # Mock execute to simulate worker usage
    async def mock_execute_async(action, params):
        return {
            "status": "success",
            "output": "Hello from remote server!",
            "action": action,
            "provider": "ssh",
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


def test_with_real_ssh():
    """Test with real SSH if available and enabled."""
    print("\n[TEST 12] Real SSH execution (optional)")

    # Skip if not enabled
    if not os.environ.get("TEST_WITH_REAL_SSH"):
        print("  [SKIP] Set TEST_WITH_REAL_SSH=1 to run real SSH tests")
        return

    # Get test SSH configuration from environment
    ssh_host = os.environ.get("SSH_TEST_HOST", "localhost")
    ssh_username = os.environ.get("SSH_TEST_USERNAME", os.getenv("USER", "ubuntu"))
    ssh_key_path = os.environ.get("SSH_TEST_KEY_PATH")

    provider = SSHProvider(
        host=ssh_host,
        username=ssh_username,
        key_path=ssh_key_path,
    )

    # Test health check
    health = provider.health_check()
    if health["status"] != "healthy":
        print(f"  [SKIP] SSH not available: {health.get('error', 'unknown')}")
        return

    print(f"  [INFO] SSH is available to {ssh_host}, running real test...")

    # Test simple command
    result = provider.execute("execute_command", {"command": "echo 'Hello from SSH!'"})

    assert result["status"] == "success"
    assert result["exit_code"] == 0
    assert "Hello from SSH" in result["output"]
    print("  [PASS] Real SSH execution successful")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("SSHProvider Tests")
    print("=" * 60)

    try:
        test_ssh_provider_initialization()
        test_action_to_command_mapping()
        test_build_ssh_command()
        test_execute_action_success()
        test_execute_action_failure()
        test_execute_timeout()
        test_health_check_ssh_available()
        test_health_check_ssh_connection_failed()
        test_health_check_ssh_not_installed()
        test_cancel_job()
        test_worker_integration()
        test_with_real_ssh()

        print("\n" + "=" * 60)
        print("[SUCCESS] All SSHProvider tests passed!")
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
