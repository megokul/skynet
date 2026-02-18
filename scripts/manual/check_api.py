"""Test SKYNET FastAPI service."""

import asyncio
import json
import sys
from uuid import uuid4

import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

API_BASE = "http://localhost:8000"


async def test_health():
    """Test health endpoint."""
    print("\n=== Testing /v1/health ===")
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{API_BASE}/v1/health")
        print(f"Status: {response.status_code}")
        print(json.dumps(response.json(), indent=2))
        return response.status_code == 200


async def test_plan():
    """Test plan generation endpoint."""
    print("\n=== Testing /v1/plan ===")

    request_data = {
        "request_id": str(uuid4()),
        "user_message": "Check git status and list all modified files",
        "context": {
            "repo": "https://github.com/user/repo",
            "branch": "main",
            "environment": "dev",
            "recent_actions": [],
        },
        "constraints": {
            "max_cost_usd": 1.50,
            "time_budget_min": 30,
            "allowed_targets": ["laptop"],
            "requires_approval_for": ["deploy_prod", "send_email"],
        },
    }

    print("Request:")
    print(json.dumps(request_data, indent=2))

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                f"{API_BASE}/v1/plan",
                json=request_data,
            )
            print(f"\nStatus: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                print("\nResponse:")
                print(json.dumps(result, indent=2))
                return True
            else:
                print(f"Error: {response.text}")
                return False

        except Exception as e:
            print(f"Error: {e}")
            return False


async def test_policy_check():
    """Test policy check endpoint."""
    print("\n=== Testing /v1/policy/check ===")

    request_data = {
        "action": "git_status",
        "target": "laptop",
        "context": {},
    }

    print("Request:")
    print(json.dumps(request_data, indent=2))

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/v1/policy/check",
            json=request_data,
        )
        print(f"\nStatus: {response.status_code}")
        print(json.dumps(response.json(), indent=2))
        return response.status_code == 200


async def test_report():
    """Test report endpoint."""
    print("\n=== Testing /v1/report ===")

    request_data = {
        "request_id": str(uuid4()),
        "step_reports": [
            {
                "step": 1,
                "status": "completed",
                "started_at": "2026-02-16T10:00:00Z",
                "completed_at": "2026-02-16T10:05:00Z",
                "output": "Git status executed successfully",
                "error": None,
                "artifacts_uploaded": ["s3://bucket/runs/123/git_status.txt"],
            }
        ],
        "overall_status": "completed",
        "metadata": {},
    }

    print("Request:")
    print(json.dumps(request_data, indent=2))

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/v1/report",
            json=request_data,
        )
        print(f"\nStatus: {response.status_code}")
        print(json.dumps(response.json(), indent=2))
        return response.status_code == 200


async def main():
    """Run all tests."""
    print("=" * 70)
    print("SKYNET FastAPI Service Tests")
    print("=" * 70)

    results = []

    # Test health
    results.append(("Health Check", await test_health()))

    # Test policy check
    results.append(("Policy Check", await test_policy_check()))

    # Test report
    results.append(("Report", await test_report()))

    # Test plan (this requires Planner to be initialized)
    results.append(("Plan Generation", await test_plan()))

    # Summary
    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)
    for name, passed in results:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{status} - {name}")

    all_passed = all(passed for _, passed in results)
    print("\n" + ("All tests passed!" if all_passed else "Some tests failed"))
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
