"""
CHATHAN — Mock Execution Provider

A mock provider for testing that simulates action execution
without actually performing any operations.

Useful for:
- Testing the execution flow
- Demos without side effects
- Development without real infrastructure
"""

from __future__ import annotations

import logging
import time
from typing import Any

from skynet.chathan.providers.base_provider import BaseExecutionProvider

logger = logging.getLogger("skynet.chathan.providers.mock")


class MockProvider(BaseExecutionProvider):
    """
    Mock execution provider that simulates action execution.

    All actions return success with simulated output.
    """

    def __init__(self):
        """Initialize mock provider."""
        super().__init__()
        self.name = "mock"
        logger.info("Mock provider initialized")

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Simulate action execution.

        Args:
            action: Action type (e.g., 'git_status', 'run_tests')
            params: Action parameters

        Returns:
            Simulated execution result
        """
        logger.info(f"[MOCK] Executing {action} with params: {params}")

        # Simulate some work
        time.sleep(0.1)

        # Generate mock output based on action type
        output = self._generate_mock_output(action, params)

        return {
            "status": "success",
            "output": output,
            "action": action,
            "provider": "mock",
        }

    def health_check(self) -> dict[str, Any]:
        """Mock provider health check."""
        return {
            "status": "healthy",
            "provider": "mock",
            "capabilities": ["all_actions"],
        }

    def cancel(self, execution_id: str) -> dict[str, Any]:
        """Mock cancellation."""
        return {
            "status": "cancelled",
            "execution_id": execution_id,
            "provider": "mock",
        }

    def _generate_mock_output(self, action: str, params: dict[str, Any]) -> str:
        """Generate realistic mock output for different action types."""
        mock_outputs = {
            "git_status": """On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  modified:   skynet/core/planner.py
  modified:   skynet/core/dispatcher.py

no changes added to commit""",
            "run_tests": """============================= test session starts ==============================
platform win32 -- Python 3.13.0
collected 24 items

test_planner.py ........                                                     [ 33%]
test_dispatcher.py ........                                                  [ 66%]
test_orchestrator.py ........                                                [100%]

============================== 24 passed in 1.23s ===============================""",
            "list_directory": """total 48
drwxr-xr-x  12 user  staff   384 Feb 15 10:30 skynet/
-rw-r--r--   1 user  staff  1024 Feb 15 10:15 README.md
-rw-r--r--   1 user  staff   512 Feb 15 10:20 .env
-rw-r--r--   1 user  staff  2048 Feb 15 10:25 main.py""",
            "docker_build": """Sending build context to Docker daemon  15.36kB
Step 1/5 : FROM python:3.13-slim
 ---> a1b2c3d4e5f6
Step 2/5 : WORKDIR /app
 ---> Running in abc123def456
Successfully built abc123def456
Successfully tagged myapp:latest""",
            "execute_command": f"""[MOCK] Command executed: {params.get('command', 'unknown')}
Output: Command completed successfully""",
        }

        return mock_outputs.get(action, f"[MOCK] Executed {action} successfully\nParams: {params}")
