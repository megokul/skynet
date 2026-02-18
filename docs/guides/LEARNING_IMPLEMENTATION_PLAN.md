# SKYNET — Learning-Focused Implementation Plan

**Approach**: Build fresh from scratch for deep architectural understanding
**Reference Code**: Use openclaw-gateway as examples to learn from
**Priority**: Learning > Speed
**Goal**: Production-ready system with features from openclaw-gateway

---

## 🎓 Learning Philosophy

Each phase includes:
- **🏗️ What We're Building** - The component and its purpose
- **📚 Architectural Concepts** - Why it's designed this way
- **💡 Reference Code** - Working examples from openclaw-gateway
- **🔨 Implementation Steps** - How to build it
- **✅ Learning Checkpoints** - Verify understanding before moving on

---

## Phase 1: Foundation — The Brain's Core (SKYNET Core)

**Duration**: 5-7 days (learning-focused)
**Complexity**: High
**Learning Value**: ⭐⭐⭐⭐⭐ (This is the heart of the system)

---

### 1.1 — Build the Planner (AI-Powered Task Breakdown)

#### 🏗️ **What We're Building**

A component that takes user intent (natural language) and generates a structured PlanSpec.

**Input**: "Deploy the ProTech bot to production"
**Output**: PlanSpec with human-readable steps:
```python
PlanSpec(
    job_id="job_abc123",
    user_intent="Deploy the ProTech bot to production",
    proposed_steps=[
        {"description": "Check current git status", "risk": "READ_ONLY"},
        {"description": "Run tests to verify everything passes", "risk": "READ_ONLY"},
        {"description": "Build production Docker image", "risk": "WRITE"},
        {"description": "Push image to registry", "risk": "WRITE"},
        {"description": "Deploy to production server", "risk": "ADMIN"},
    ],
    estimated_risk_level="ADMIN",
    expected_artifacts=["build-log.txt", "deployment-receipt"]
)
```

---

#### 📚 **Architectural Concepts to Learn**

**1. Why Separate Planning from Execution?**

```
User Intent  →  PlanSpec  →  ExecutionSpec  →  Execution
(vague)         (human)      (machine)          (action)

"deploy bot"  →  1. Test    →  git_status     →  $ git status
                 2. Build       run_tests          $ pytest
                 3. Deploy      docker_build       $ docker build
```

**Key insight**:
- **PlanSpec** = What the user approves (high-level, readable)
- **ExecutionSpec** = What the machine executes (low-level, precise)
- This separation enables **approval workflows** and **safety checks**

**2. Why Use AI for Planning?**

Traditional approach (brittle):
```python
if "deploy" in user_intent and "bot" in user_intent:
    return ["test", "build", "deploy"]  # Too simple!
```

AI approach (flexible):
```python
# AI understands context, dependencies, and best practices
response = await ai.chat([
    {"role": "user", "content": f"Plan this task: {user_intent}"}
])
# AI returns: "First test, then build, then deploy with health checks"
```

**Learning**: AI can reason about task dependencies, edge cases, and project context.

---

#### 💡 **Reference Code from openclaw-gateway**

**See**: [openclaw-gateway/orchestrator/project_manager.py:96-179](openclaw-gateway/orchestrator/project_manager.py:96-179)

**Key sections to study**:

1. **Prompt Engineering** (lines 110-121):
```python
messages = [{
    "role": "user",
    "content": (
        f"Create a detailed implementation plan for this project idea:\n\n"
        f"{idea_text}\n\n"
        f"The project name is: {project['display_name']}\n"
        f"Output ONLY the JSON plan, no other text."
    ),
}]
```

**Learning**: Prompt structure matters. Notice:
- Clear instructions ("Create a detailed plan")
- Context (project name, ideas)
- Output format requirement ("ONLY JSON")

2. **AI Tool Loop** (lines 123-126):
```python
final_text, updated_messages = await self._planning_loop(
    messages, system_prompt,
)
```

**Learning**: Planning isn't one-shot. The AI might:
- Ask for web search results
- Request file contents
- Iterate on the plan

3. **JSON Parsing with Resilience** (lines 366-390):
```python
def _parse_plan_json(self, text: str) -> dict | None:
    # Try direct parse
    try:
        return json.loads(text)
    except:
        pass

    # Try extracting from ```json ... ```
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    ...
```

**Learning**: AI responses are unpredictable. Always handle:
- Markdown code blocks
- Extra text before/after JSON
- Malformed JSON

---

#### 🔨 **Implementation Steps**

**Step 1: Create the file structure**

```bash
# Create the planner module
mkdir -p skynet/core
touch skynet/core/__init__.py
touch skynet/core/planner.py
```

**Step 2: Implement the Planner class**

**File**: `skynet/core/planner.py`

```python
"""
SKYNET Core — Planner

Converts user intent into human-readable PlanSpec using AI.
This is the first step in the job lifecycle.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from skynet.chathan.protocol.plan_spec import PlanSpec
from skynet.policy.engine import PolicyEngine

logger = logging.getLogger("skynet.core.planner")


class Planner:
    """
    Uses AI to decompose user intent into a structured plan.

    Workflow:
    1. Accept user intent (natural language)
    2. Call AI with planning prompt
    3. Parse AI response into PlanSpec
    4. Classify risk levels
    5. Return PlanSpec for user approval
    """

    def __init__(self, ai_client, policy_engine: PolicyEngine):
        """
        Args:
            ai_client: AI provider (Anthropic, OpenAI, etc.)
            policy_engine: For risk classification
        """
        self.ai = ai_client
        self.policy = policy_engine

    async def generate_plan(
        self,
        job_id: str,
        user_intent: str,
        project_context: dict[str, Any] | None = None,
    ) -> PlanSpec:
        """
        Generate a PlanSpec from user intent.

        Args:
            job_id: Unique job identifier
            user_intent: User's task description
            project_context: Optional context (working_dir, tech_stack, etc.)

        Returns:
            PlanSpec ready for user approval
        """
        logger.info(f"Generating plan for job {job_id}: {user_intent[:50]}...")

        # Build the planning prompt
        prompt = self._build_prompt(user_intent, project_context or {})

        # Call AI (with retry logic for tool use)
        ai_response = await self._call_ai_with_tools(prompt)

        # Parse JSON from response
        plan_data = self._parse_json(ai_response)
        if not plan_data:
            raise ValueError(f"AI returned invalid JSON: {ai_response[:200]}")

        # Convert AI plan to PlanSpec
        plan_spec = self._ai_to_plan_spec(job_id, user_intent, plan_data)

        logger.info(
            f"Plan generated: {len(plan_spec.steps)} steps, "
            f"risk={plan_spec.max_risk_level}"
        )

        return plan_spec

    def _build_prompt(self, user_intent: str, context: dict) -> str:
        """
        Build the AI planning prompt.

        Prompt engineering tips:
        - Be specific about output format
        - Provide examples
        - Include constraints
        - Give context
        """
        working_dir = context.get("working_dir", "~/projects")
        tech_stack = context.get("tech_stack", "unknown")

        prompt = f"""You are SKYNET, an autonomous task orchestrator.

USER REQUEST:
{user_intent}

CONTEXT:
- Working directory: {working_dir}
- Tech stack: {tech_stack}

TASK:
Generate a detailed step-by-step plan to accomplish this request.

OUTPUT FORMAT (JSON):
{{
  "summary": "One-sentence description of the plan",
  "steps": [
    {{
      "title": "Step name",
      "description": "What this step does",
      "risk_level": "READ_ONLY|WRITE|ADMIN",
      "estimated_minutes": 5
    }}
  ],
  "artifacts": ["expected output files"],
  "total_estimated_minutes": 30
}}

RISK LEVELS:
- READ_ONLY: Only reads/inspects (git status, run tests, check files)
- WRITE: Modifies files/state (create files, install deps, build)
- ADMIN: Critical operations (deploy, git push, delete, system changes)

RULES:
1. Break down into clear, atomic steps
2. Include verification steps (tests, health checks)
3. Order steps logically (dependencies first)
4. Be specific about what each step does
5. Estimate realistic time for each step
6. Only return JSON, no other text

Generate the plan now:
"""
        return prompt

    async def _call_ai_with_tools(self, prompt: str) -> str:
        """
        Call AI, handling potential tool use (web search, file read).

        Learning: Planning might need external data:
        - Web search for docs/tutorials
        - File read to understand project structure
        - Code search to find patterns
        """
        messages = [{"role": "user", "content": prompt}]

        # Allow up to 3 rounds of tool use
        for round_num in range(3):
            response = await self.ai.chat(
                messages=messages,
                max_tokens=4096,
                temperature=0.7,
            )

            # If no tool calls, we're done
            if not hasattr(response, 'tool_calls') or not response.tool_calls:
                return response.text

            # Handle tool calls
            logger.debug(f"AI requested {len(response.tool_calls)} tools")

            # Add assistant message with tool calls
            messages.append({
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": response.tool_calls,
            })

            # Execute tools and add results
            tool_results = []
            for tool_call in response.tool_calls:
                result = await self._execute_tool(tool_call)
                tool_results.append({
                    "tool_call_id": tool_call.id,
                    "output": result,
                })

            messages.append({
                "role": "tool",
                "content": tool_results,
            })

        # If we exhausted rounds, return last response
        return response.text

    async def _execute_tool(self, tool_call) -> str:
        """
        Execute a tool requested by the AI.

        Common planning tools:
        - web_search: Look up docs, tutorials, best practices
        - file_read: Check project structure
        - git_log: Understand recent changes
        """
        tool_name = tool_call.name
        tool_input = tool_call.input

        if tool_name == "web_search":
            # Implement web search or disable
            return "Web search not available during planning"
        elif tool_name == "file_read":
            # Implement file read or disable
            return "File read not available during planning"
        else:
            return f"Tool {tool_name} not available"

    def _parse_json(self, text: str) -> dict[str, Any] | None:
        """
        Extract JSON from AI response.

        Handles:
        - Plain JSON
        - JSON in markdown code blocks
        - JSON buried in text
        """
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

        logger.warning(f"Failed to parse JSON from AI response: {text[:200]}")
        return None

    def _ai_to_plan_spec(
        self,
        job_id: str,
        user_intent: str,
        plan_data: dict,
    ) -> PlanSpec:
        """
        Convert AI's JSON plan to formal PlanSpec.

        PlanSpec is the contract between planner and dispatcher.
        """
        steps = []
        max_risk = "READ_ONLY"

        for step_data in plan_data.get("steps", []):
            risk = step_data.get("risk_level", "WRITE").upper()

            # Track highest risk level
            if self._risk_exceeds(risk, max_risk):
                max_risk = risk

            steps.append({
                "title": step_data.get("title", "Untitled step"),
                "description": step_data.get("description", ""),
                "risk_level": risk,
                "estimated_minutes": step_data.get("estimated_minutes", 5),
            })

        return PlanSpec(
            job_id=job_id,
            user_intent=user_intent,
            summary=plan_data.get("summary", ""),
            steps=steps,
            artifacts=plan_data.get("artifacts", []),
            max_risk_level=max_risk,
            total_estimated_minutes=plan_data.get("total_estimated_minutes", 0),
            agent_roles_needed=plan_data.get("agent_roles", []),
        )

    def _risk_exceeds(self, risk1: str, risk2: str) -> bool:
        """Check if risk1 is higher than risk2."""
        levels = {"READ_ONLY": 0, "WRITE": 1, "ADMIN": 2, "BLOCKED": 3}
        return levels.get(risk1, 1) > levels.get(risk2, 1)
```

---

**Step 3: Update PlanSpec model to match**

We need to ensure `skynet/chathan/protocol/plan_spec.py` has the right structure.

Check if it matches the planner output. If not, update it:

```python
# skynet/chathan/protocol/plan_spec.py
from dataclasses import dataclass, field
from typing import Any

@dataclass
class PlanSpec:
    """Human-readable plan for user approval."""

    job_id: str
    user_intent: str
    summary: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    max_risk_level: str = "WRITE"
    total_estimated_minutes: int = 0
    agent_roles_needed: list[str] = field(default_factory=list)

    @classmethod
    def from_ai_plan(cls, job_id: str, plan_id: str, plan_data: dict):
        """Convert AI plan JSON to PlanSpec (for compatibility)."""
        # You can reference openclaw-gateway implementation
        pass

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "user_intent": self.user_intent,
            "summary": self.summary,
            "steps": self.steps,
            "artifacts": self.artifacts,
            "max_risk_level": self.max_risk_level,
            "total_estimated_minutes": self.total_estimated_minutes,
            "agent_roles_needed": self.agent_roles_needed,
        }

    def to_markdown(self) -> str:
        """Format for Telegram display."""
        lines = [
            f"📋 **Plan: {self.summary}**",
            "",
            f"**Risk Level**: {self.max_risk_level}",
            f"**Estimated Time**: {self.total_estimated_minutes} minutes",
            "",
            "**Steps**:",
        ]

        for i, step in enumerate(self.steps, 1):
            risk_emoji = {
                "READ_ONLY": "👁️",
                "WRITE": "✏️",
                "ADMIN": "⚠️",
            }.get(step.get("risk_level", "WRITE"), "")

            lines.append(
                f"{i}. {risk_emoji} **{step.get('title')}**\n"
                f"   {step.get('description')}\n"
                f"   (~{step.get('estimated_minutes', '?')} min)"
            )

        if self.artifacts:
            lines.extend(["", "**Expected Output**:"])
            for artifact in self.artifacts:
                lines.append(f"  - {artifact}")

        return "\n".join(lines)
```

---

**Step 4: Set up AI client**

Create a simple AI client wrapper:

```python
# skynet/ai/client.py
"""AI client for planning."""

import os
from anthropic import AsyncAnthropic

class AIClient:
    """Wrapper for Anthropic Claude."""

    def __init__(self, api_key: str | None = None):
        self.client = AsyncAnthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY")
        )

    async def chat(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ):
        """Simple chat completion."""
        response = await self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
        )

        # Return simple response object
        class Response:
            def __init__(self, content):
                self.text = content[0].text if content else ""
                self.tool_calls = []  # Handle later

        return Response(response.content)
```

---

**Step 5: Write a test**

```python
# skynet/core/test_planner.py
"""Test the planner."""

import asyncio
from skynet.core.planner import Planner
from skynet.policy.engine import PolicyEngine
from skynet.ai.client import AIClient

async def test_planner():
    ai = AIClient()
    policy = PolicyEngine()
    planner = Planner(ai, policy)

    plan = await planner.generate_plan(
        job_id="test_001",
        user_intent="Check git status and run tests",
        project_context={"working_dir": "~/projects/myapp"},
    )

    print("=== Generated Plan ===")
    print(plan.to_markdown())
    print("\n=== Plan Dict ===")
    print(plan.to_dict())

if __name__ == "__main__":
    asyncio.run(test_planner())
```

---

#### ✅ **Learning Checkpoints**

Before moving to the next component, verify you understand:

- [ ] **Why we separate PlanSpec from ExecutionSpec**
  - Quiz: What happens if we skip PlanSpec and go straight to execution?
  - Answer: No approval workflow, user can't see what will happen

- [ ] **How prompt engineering affects plan quality**
  - Experiment: Change the prompt, see how plans differ
  - Try: Remove the "RULES" section, see if plans get worse

- [ ] **Why JSON parsing needs resilience**
  - Test: Send malformed prompts, see how parser handles it
  - Understand: AI responses aren't deterministic

- [ ] **Role of the policy engine in risk classification**
  - Read: `skynet/policy/engine.py`
  - Understand: How risk levels map to approval requirements

---

### Next Components (Preview)

Once you've built and understood the **Planner**, we'll build:

**1.2 — Dispatcher** (PlanSpec → ExecutionSpec converter)
**1.3 — Orchestrator** (State machine coordinator)
**1.4 — Main entry point** (Wire everything together)

---

## 🎯 Ready to Start?

Let me know when you're ready to:
1. **Build the Planner** — I can guide you through each step
2. **Migrate AI provider from openclaw-gateway** — Use the ProviderRouter
3. **Test the Planner** — Run it and see a real plan generated

Or if you want to jump to a different component first, I can adjust the plan.

**What would you like to do next?**
