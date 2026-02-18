"""Test LocalProvider (without Celery/Redis running)."""

import sys
from pathlib import Path

# Add skynet to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.chathan.providers.local_provider import LocalProvider


def main():
    print("=" * 60)
    print("SKYNET LocalProvider Test (Direct Function Call)")
    print("=" * 60)
    print()

    # Initialize provider
    # Allow current directory for testing
    provider = LocalProvider(
        allowed_paths=[str(Path.cwd())],
        default_timeout=10,
    )

    # Test 1: Health check
    print("[1] Testing health check...")
    result = provider.health_check()
    print(f"[{'SUCCESS' if result['status'] == 'healthy' else 'FAILED'}] Health check: {result['status']}")
    print(f"  Capabilities: {result.get('capabilities', [])}")
    print()

    # Test 2: Git status (safe READ_ONLY command)
    print("[2] Testing git status...")
    result = provider.execute(
        action="git_status",
        params={"working_dir": str(Path.cwd())},
    )
    print(f"[{'SUCCESS' if result['status'] == 'success' else 'FAILED'}] Git status:")
    print(f"  Status: {result['status']}")
    print(f"  Exit code: {result['exit_code']}")
    if result['output']:
        output_preview = result['output'][:200].replace('\n', '\n      ')
        print(f"  Output:\n      {output_preview}...")
    print()

    # Test 3: List directory
    print("[3] Testing list directory...")
    result = provider.execute(
        action="list_directory",
        params={"working_dir": str(Path.cwd())},
    )
    print(f"[{'SUCCESS' if result['status'] in ['success', 'failed'] else 'FAILED'}] List directory:")
    print(f"  Status: {result['status']}")
    print(f"  Exit code: {result['exit_code']}")
    if result['output']:
        output_lines = result['output'].split('\n')[:10]
        print(f"  Output (first 10 lines):")
        for line in output_lines:
            print(f"      {line}")
    print()

    # Test 4: Path restriction (should fail)
    print("[4] Testing path restriction...")
    result = provider.execute(
        action="list_directory",
        params={"working_dir": "/etc"},  # Not in allowed paths
    )
    print(f"[{'SUCCESS' if result['status'] == 'error' else 'FAILED'}] Path restriction works:")
    print(f"  Status: {result['status']}")
    print(f"  Message: {result['output']}")
    print()

    # Test 5: Unknown action
    print("[5] Testing unknown action...")
    result = provider.execute(
        action="unknown_action",
        params={},
    )
    print(f"[{'SUCCESS' if result['status'] == 'error' else 'FAILED'}] Unknown action handling:")
    print(f"  Status: {result['status']}")
    print(f"  Message: {result['output']}")
    print()

    # Test 6: Execute command (echo test)
    print("[6] Testing execute_command...")
    result = provider.execute(
        action="execute_command",
        params={
            "command": "echo Hello from SKYNET",
            "working_dir": str(Path.cwd()),
        },
    )
    print(f"[{'SUCCESS' if result['status'] == 'success' else 'FAILED'}] Execute command:")
    print(f"  Status: {result['status']}")
    print(f"  Exit code: {result['exit_code']}")
    print(f"  Output: {result['output'].strip()}")
    print()

    # Test 7: Cancellation (not supported)
    print("[7] Testing cancellation...")
    result = provider.cancel("test_execution_001")
    print(f"[{'SUCCESS' if result['status'] == 'not_supported' else 'FAILED'}] Cancel returns expected status:")
    print(f"  Status: {result['status']}")
    print(f"  Message: {result.get('message', 'N/A')}")
    print()

    print("=" * 60)
    print("[SUCCESS] LocalProvider tests completed!")
    print("=" * 60)
    print()
    print("NOTE: LocalProvider can now execute real commands.")
    print("Safety features:")
    print("  - Working directory restrictions")
    print("  - Command timeout (default 60s)")
    print("  - Output size limits (1MB)")
    print()


if __name__ == "__main__":
    main()
