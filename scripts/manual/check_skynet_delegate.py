"""
Manual integration tests for OpenClaw SKYNET delegate skill.
"""

import asyncio
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "openclaw-gateway"))

from skills.base import SkillContext
from skills.skynet_delegate import SkynetDelegateSkill


async def test_route_task() -> bool:
    print("\n" + "=" * 70)
    print("Test 1: skynet_route_task")
    print("=" * 70)

    skill = SkynetDelegateSkill()
    context = SkillContext(
        project_id="test-project",
        project_path="/tmp/test",
        gateway_api_url="http://localhost:8766",
    )
    tool_input = {
        "action": "git_status",
        "params": {"working_dir": "."},
        "confirmed": True,
    }

    print("Calling SKYNET /v1/route-task endpoint...")
    try:
        result = await skill.execute("skynet_route_task", tool_input, context)
        print("\nResult:")
        print(result)
        print("\n[PASS] - Route task completed")
        return True
    except Exception as exc:
        print(f"\n[FAIL] - Route task failed: {exc}")
        return False


async def test_system_state() -> bool:
    print("\n" + "=" * 70)
    print("Test 2: skynet_system_state")
    print("=" * 70)

    skill = SkynetDelegateSkill()
    context = SkillContext(
        project_id="test-project",
        project_path="/tmp/test",
        gateway_api_url="http://localhost:8766",
    )

    print("Calling SKYNET /v1/system-state endpoint...")
    try:
        result = await skill.execute("skynet_system_state", {}, context)
        print("\nResult:")
        print(result)
        print("\n[PASS] - System state fetched")
        return True
    except Exception as exc:
        print(f"\n[FAIL] - System state fetch failed: {exc}")
        return False


async def test_tools_definition() -> bool:
    print("\n" + "=" * 70)
    print("Test 3: Tool Definitions")
    print("=" * 70)

    skill = SkynetDelegateSkill()
    tools = skill.get_tools()
    expected_tools = {"skynet_route_task", "skynet_system_state"}
    names = {tool["name"] for tool in tools}

    print(f"Skill: {skill.name}")
    print(f"Version: {skill.version}")
    print(f"Tools: {sorted(names)}")

    if names != expected_tools:
        print(f"[FAIL] - Unexpected tool set: {sorted(names)}")
        return False

    print("[PASS] - Tool definitions valid")
    return True


async def main() -> int:
    print("=" * 70)
    print("SKYNET Delegate Skill Tests")
    print("=" * 70)
    print("\nPrerequisites:")
    print("  1. SKYNET FastAPI server running on port 8000")
    print("  2. OpenClaw gateway running on port 8766")
    print("  3. Optional: SKYNET_API_KEY set if diagnostics are protected")

    results = [
        ("Tool Definitions", await test_tools_definition()),
        ("System State", await test_system_state()),
        ("Route Task", await test_route_task()),
    ]

    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)
    for name, passed in results:
        print(f"{'[PASS]' if passed else '[FAIL]'} - {name}")

    all_passed = all(passed for _, passed in results)
    print("\n" + ("All tests passed!" if all_passed else "Some tests failed"))
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
