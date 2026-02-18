"""
Test SKYNET Delegate Skill - Integration Test.

Tests the OpenClaw skill that calls SKYNET control plane API.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
repo_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "openclaw-gateway"))

from skills.skynet_delegate import SkynetDelegateSkill
from skills.base import SkillContext


async def test_skynet_plan():
    """Test requesting a plan from SKYNET."""
    print("\n" + "=" * 70)
    print("Test 1: Request Plan from SKYNET")
    print("=" * 70)

    # Create skill
    skill = SkynetDelegateSkill()

    # Create mock context
    context = SkillContext(
        project_id="test-project",
        project_path="/tmp/test",
        gateway_api_url="http://localhost:8766",
    )

    # Test input
    tool_input = {
        "user_message": "Check git status and list all modified files",
        "context": {
            "repo": "https://github.com/user/repo",
            "branch": "main",
            "environment": "dev",
        },
        "constraints": {
            "max_cost_usd": 1.50,
            "time_budget_min": 30,
            "allowed_targets": ["laptop"],
        },
    }

    print(f"\nUser Message: {tool_input['user_message']}")
    print("\nCalling SKYNET /v1/plan endpoint...")

    try:
        result = await skill.execute("skynet_plan", tool_input, context)
        print("\nResult:")
        print(result)
        print("\n[PASS] - Plan request completed")
        return True
    except Exception as e:
        print(f"\n[FAIL] - Plan request failed: {e}")
        return False


async def test_policy_check():
    """Test checking policy."""
    print("\n" + "=" * 70)
    print("Test 2: Check Policy")
    print("=" * 70)

    skill = SkynetDelegateSkill()
    context = SkillContext(
        project_id="test-project",
        project_path="/tmp/test",
        gateway_api_url="http://localhost:8766",
    )

    tool_input = {
        "action": "git_status",
        "target": "laptop",
    }

    print(f"\nAction: {tool_input['action']}")
    print("Calling SKYNET /v1/policy/check endpoint...")

    try:
        result = await skill.execute("skynet_policy_check", tool_input, context)
        print("\nResult:")
        print(result)
        print("\n[PASS] - Policy check completed")
        return True
    except Exception as e:
        print(f"\n[FAIL] - Policy check failed: {e}")
        return False


async def test_tools_definition():
    """Test tool definitions."""
    print("\n" + "=" * 70)
    print("Test 3: Tool Definitions")
    print("=" * 70)

    skill = SkynetDelegateSkill()
    tools = skill.get_tools()

    print(f"\nSkill: {skill.name}")
    print(f"Description: {skill.description}")
    print(f"Version: {skill.version}")
    print(f"\nTools ({len(tools)}):")

    for tool in tools:
        print(f"  - {tool['name']}: {tool['description']}")

    print("\n[PASS] - Tool definitions valid")
    return True


async def main():
    """Run all tests."""
    print("=" * 70)
    print("SKYNET Delegate Skill Tests")
    print("=" * 70)
    print("\nPrerequisites:")
    print("  1. SKYNET FastAPI server running on port 8000")
    print("  2. SKYNET_ORCHESTRATOR_URL environment variable set (optional)")
    print("\nStarting tests...")

    results = []

    # Test tool definitions (always works)
    results.append(("Tool Definitions", await test_tools_definition()))

    # Test policy check (requires SKYNET API)
    results.append(("Policy Check", await test_policy_check()))

    # Test plan request (requires SKYNET API with Planner initialized)
    results.append(("Plan Request", await test_skynet_plan()))

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
