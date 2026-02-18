"""Simple test without emojis - Windows compatible."""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from skynet.core.planner import Planner

load_dotenv()


async def main():
    print("=" * 60)
    print("SKYNET Planner Test")
    print("=" * 60)

    api_key = os.getenv("GOOGLE_AI_API_KEY")
    planner = Planner(api_key=api_key)

    # Single test
    print("\nUser Intent: Check git status and list all modified files")
    print("-" * 60)

    plan = await planner.generate_plan(
        job_id="test_001",
        user_intent="Check git status and list all modified files",
        context={"working_dir": "~/projects/myapp"},
    )

    print("\n[SUCCESS] Plan Generated!\n")
    print(f"Summary: {plan['summary']}")
    print(f"Risk Level: {plan['max_risk_level']}")
    print(f"Estimated Time: {plan['total_estimated_minutes']} minutes")
    print(f"\nSteps ({len(plan['steps'])}):\n")

    for i, step in enumerate(plan['steps'], 1):
        print(f"{i}. {step['title']} [{step['risk_level']}]")
        print(f"   {step['description']}")
        print(f"   Time: ~{step['estimated_minutes']} min\n")

    if plan.get('artifacts'):
        print("Expected Artifacts:")
        for artifact in plan['artifacts']:
            print(f"  - {artifact}")

    print("\n" + "=" * 60)
    print("Full Plan JSON:")
    print("=" * 60)
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
