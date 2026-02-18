# SKYNET — Quick Start with Gemini

**Goal**: Build and test your first component (the Planner) in 30 minutes

---

## 🚀 Step 1: Set Up Environment (5 min)

### Install Dependencies

```bash
# Navigate to skynet directory
cd skynet

# Create virtual environment (if not already)
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

# Install required packages
pip install google-generativeai python-dotenv
```

### Set Up API Key

Create `.env` file in `skynet/` directory:

```bash
# skynet/.env
GOOGLE_AI_API_KEY=your_gemini_api_key_here
```

Test it works:
```bash
python -c "from google import genai; print('Gemini SDK installed!')"
```

---

## 🏗️ Step 2: Build the Planner (15 min)

### Create Core Module

```bash
mkdir -p skynet/core
touch skynet/core/__init__.py
```

### Create the Planner

**File**: `skynet/core/planner.py`

Copy this complete implementation:

```python
"""
SKYNET Core — Planner

Converts user intent into structured PlanSpec using Gemini.
"""

from __future__ import annotations

import json
import logging
import re
import os
from typing import Any

from google import genai
from google.genai import types

logger = logging.getLogger("skynet.core.planner")


class Planner:
    """
    Uses Gemini AI to decompose user intent into a structured plan.

    Example:
        planner = Planner(api_key="your_key")
        plan = await planner.generate_plan(
            job_id="job_001",
            user_intent="Deploy the bot to production"
        )
    """

    def __init__(self, api_key: str | None = None, model: str = "gemini-2.0-flash-exp"):
        """
        Args:
            api_key: Gemini API key (or set GOOGLE_AI_API_KEY env var)
            model: Gemini model to use (default: gemini-2.0-flash-exp)
        """
        self.api_key = api_key or os.getenv("GOOGLE_AI_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_AI_API_KEY not set")

        self.client = genai.Client(api_key=self.api_key)
        self.model_name = model

        logger.info(f"Planner initialized with Gemini {model}")

    async def generate_plan(
        self,
        job_id: str,
        user_intent: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Generate a structured plan from user intent.

        Args:
            job_id: Unique job identifier
            user_intent: User's task description
            context: Optional context (working_dir, tech_stack, etc.)

        Returns:
            PlanSpec dict with steps, risk levels, artifacts
        """
        logger.info(f"Generating plan for: {user_intent[:50]}...")

        # Build prompt
        prompt = self._build_prompt(user_intent, context or {})

        # Call Gemini
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=8192,
                    temperature=0.7,
                ),
            )

            # Extract text
            text = response.text
            logger.debug(f"Gemini response: {len(text)} chars")

        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise

        # Parse JSON
        plan_data = self._parse_json(text)
        if not plan_data:
            raise ValueError(f"Failed to parse plan JSON from: {text[:200]}")

        # Build PlanSpec
        plan_spec = {
            "job_id": job_id,
            "user_intent": user_intent,
            "summary": plan_data.get("summary", ""),
            "steps": plan_data.get("steps", []),
            "artifacts": plan_data.get("artifacts", []),
            "max_risk_level": self._calculate_max_risk(plan_data.get("steps", [])),
            "total_estimated_minutes": plan_data.get("total_estimated_minutes", 0),
        }

        logger.info(
            f"Plan generated: {len(plan_spec['steps'])} steps, "
            f"risk={plan_spec['max_risk_level']}"
        )

        return plan_spec

    def _build_prompt(self, user_intent: str, context: dict) -> str:
        """Build the planning prompt for Gemini."""
        working_dir = context.get("working_dir", "~/projects")

        prompt = f"""You are SKYNET, an autonomous task orchestration AI.

USER REQUEST:
{user_intent}

CONTEXT:
- Working directory: {working_dir}

TASK:
Generate a detailed step-by-step plan to accomplish this request.

OUTPUT FORMAT (JSON only, no other text):
{{
  "summary": "One-sentence description of the plan",
  "steps": [
    {{
      "title": "Step name",
      "description": "Detailed description of what this step does",
      "risk_level": "READ_ONLY",
      "estimated_minutes": 5
    }}
  ],
  "artifacts": ["list of expected output files or results"],
  "total_estimated_minutes": 30
}}

RISK LEVELS (classify each step):
- READ_ONLY: Only reads/inspects (git status, run tests, check files, list directories)
- WRITE: Modifies files/state (create files, install packages, build, compile)
- ADMIN: Critical operations (deploy, git push, delete, system changes, production actions)

RULES:
1. Break down into clear, atomic steps
2. Order steps logically (test before deploy, etc.)
3. Be specific about what each step does
4. Estimate realistic time for each step
5. Include verification/test steps where appropriate
6. Return ONLY the JSON object, no markdown, no explanation

Generate the plan now:
"""
        return prompt

    def _parse_json(self, text: str) -> dict[str, Any] | None:
        """Extract JSON from Gemini response."""
        # Try 1: Direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try 2: Extract from ```json ... ```
        match = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try 3: Find first { ... } block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        logger.warning(f"Failed to parse JSON: {text[:200]}")
        return None

    def _calculate_max_risk(self, steps: list[dict]) -> str:
        """Calculate the maximum risk level across all steps."""
        risk_levels = {"READ_ONLY": 0, "WRITE": 1, "ADMIN": 2}
        max_level = 0
        max_name = "READ_ONLY"

        for step in steps:
            risk = step.get("risk_level", "WRITE").upper()
            level = risk_levels.get(risk, 1)
            if level > max_level:
                max_level = level
                max_name = risk

        return max_name
```

---

## 🧪 Step 3: Test the Planner (10 min)

Create a test file:

**File**: `skynet/test_planner.py`

```python
"""Test the Planner with Gemini."""

import asyncio
import logging
import os
from dotenv import load_dotenv
from core.planner import Planner

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
    if not api_key:
        print("❌ Error: GOOGLE_AI_API_KEY not found in .env")
        return

    planner = Planner(api_key=api_key)

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

        # Wait between tests to avoid rate limits
        if i < len(test_tasks):
            print("\nWaiting 3 seconds before next test...")
            await asyncio.sleep(3)

    print("\n" + "=" * 60)
    print("✅ All tests complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
```

---

## ▶️ Step 4: Run It! (1 min)

```bash
# From skynet/ directory
python tests/test_planner.py
```

**Expected Output**:
```
============================================================
SKYNET Planner Test — Gemini Edition
============================================================

============================================================
Test 1: Simple Read-Only Task
============================================================
User Intent: Check git status and list all modified files

✅ Plan Generated Successfully!

📋 Summary: Check Git repository status and list modified files
⚠️  Risk Level: READ_ONLY
⏱️  Estimated Time: 5 minutes

🔧 Steps (2):

  1. 👁️ Check Git Status [READ_ONLY]
     Run 'git status' to see current repository state
     ⏱️  ~2 min

  2. 👁️ List Modified Files [READ_ONLY]
     Parse git status output to extract modified files
     ⏱️  ~3 min

📦 Expected Artifacts:
  - git_status.txt
  - modified_files_list.txt
```

---

## 🎉 Success Checklist

After running the test, you should see:

- [ ] ✅ Gemini API connection works
- [ ] ✅ Planner generates structured plans
- [ ] ✅ Risk levels are correctly classified
- [ ] ✅ Steps are clear and actionable
- [ ] ✅ Time estimates are reasonable

---

## 🐛 Troubleshooting

### Error: "GOOGLE_AI_API_KEY not found"
**Fix**: Check your `.env` file exists and has the correct key

### Error: "google-generativeai not installed"
**Fix**: `pip install google-generativeai`

### Error: "API quota exceeded"
**Fix**: Gemini free tier has limits. Wait a few minutes or check your quota at https://aistudio.google.com

### Planner returns invalid JSON
**Fix**: The prompt might need adjustment. Check Gemini's response in logs.

---

## 🎯 Next Steps

Once your Planner is working:

### **Option 1: Improve the Planner**
- Add web search capability
- Add file read capability
- Improve prompt engineering
- Add retry logic

### **Option 2: Build the Dispatcher**
- Convert PlanSpec → ExecutionSpec
- Map steps to concrete actions
- Validate with policy engine

### **Option 3: Build the Orchestrator**
- State machine for job lifecycle
- Connect Planner + Dispatcher
- Add approval workflow

### **Option 4: Integrate with Telegram**
- Display plans in Telegram
- Add approve/deny buttons
- Stream execution updates

---

## 📚 What You Learned

By building and testing the Planner, you now understand:

✅ **Architecture**: Why separate planning from execution
✅ **AI Integration**: How to use Gemini for task decomposition
✅ **Prompt Engineering**: How to get structured output from AI
✅ **Risk Classification**: READ_ONLY → WRITE → ADMIN
✅ **JSON Parsing**: Handling unpredictable AI responses
✅ **Async Python**: Using asyncio for API calls

---

## 💬 Questions?

**How does the Planner know what steps to generate?**
- The AI (Gemini) has been trained on millions of code examples
- It understands common patterns (test before deploy, etc.)
- The prompt guides it with rules and examples

**What if the plan is wrong?**
- That's why we have **user approval** (coming in Phase 3)
- User sees the PlanSpec and can approve/deny/modify

**Can I customize the planning logic?**
- Yes! Modify `_build_prompt()` to add:
  - Project-specific context
  - Tech stack information
  - Custom rules
  - Example plans

---

Ready to run it? Let me know how it goes! 🚀
