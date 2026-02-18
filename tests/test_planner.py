"""Test the Planner with Gemini."""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add skynet to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from skynet.core.planner import Planner

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)

# Load .env file
load_dotenv()


async def main():
    print("=" * 60)
    print("SKYNET Planner Test — Gemini Edition")
    print("=" * 60)

    # Create planner
    api_key = os.getenv("GOOGLE_AI_API_KEY")
    if not api_key or api_key == "your_gemini_api_key_here":
        print("\n❌ Error: GOOGLE_AI_API_KEY not set in .env file")
        print("\n📝 To fix:")
        print("   1. Get a free API key from: https://aistudio.google.com")
        print("   2. Edit .env file in project root")
        print("   3. Replace 'your_gemini_api_key_here' with your actual key")
        print()
        return

    try:
        planner = Planner(api_key=api_key)
    except Exception as e:
        print(f"\n❌ Error initializing Planner: {e}")
        return

    # Test cases
    test_tasks = [
        {
            "name": "Simple Read-Only Task",
            "intent": "Check git status and list all modified files",
            "context": {"working_dir": "~/projects/myapp"},
        },
        {
            "name": "Write Task",
            "intent": "Create a new Python file called hello.py with a simple hello world function",
            "context": {"working_dir": "~/projects/myapp"},
        },
        {
            "name": "Complex Admin Task",
            "intent": "Deploy the web application to production with health checks",
            "context": {"working_dir": "~/projects/webapp"},
        },
    ]

    for i, test in enumerate(test_tasks, 1):
        print(f"\n{'=' * 60}")
        print(f"Test {i}: {test['name']}")
        print(f"{'=' * 60}")
        print(f"User Intent: {test['intent']}\n")

        try:
            # Generate plan
            plan = await planner.generate_plan(
                job_id=f"test_{i:03d}",
                user_intent=test["intent"],
                context=test["context"],
            )

            # Display plan
            print(f"✅ Plan Generated Successfully!\n")
            print(f"📋 Summary: {plan['summary']}")
            print(f"⚠️  Risk Level: {plan['max_risk_level']}")
            print(f"⏱️  Estimated Time: {plan['total_estimated_minutes']} minutes")
            print(f"\n🔧 Steps ({len(plan['steps'])}):")

            for j, step in enumerate(plan['steps'], 1):
                risk_emoji = {
                    "READ_ONLY": "👁️",
                    "WRITE": "✏️",
                    "ADMIN": "⚠️",
                }.get(step['risk_level'], "❓")

                print(f"\n  {j}. {risk_emoji} {step['title']} [{step['risk_level']}]")
                print(f"     {step['description']}")
                print(f"     ⏱️  ~{step['estimated_minutes']} min")

            if plan.get('artifacts'):
                print(f"\n📦 Expected Artifacts:")
                for artifact in plan['artifacts']:
                    print(f"  - {artifact}")

            print()

        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()

        # Wait between tests to avoid rate limits
        if i < len(test_tasks):
            print("\nWaiting 3 seconds before next test...")
            await asyncio.sleep(3)

    print("\n" + "=" * 60)
    print("✅ All tests complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
