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

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
        memory_manager=None,
    ):
        """
        Args:
            api_key: Gemini API key (or set GOOGLE_AI_API_KEY env var)
            model: Gemini model to use (default: gemini-2.5-flash)
            memory_manager: Optional MemoryManager for experience-based planning
        """
        self.api_key = api_key or os.getenv("GOOGLE_AI_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_AI_API_KEY not set")

        self.client = genai.Client(api_key=self.api_key)
        self.model_name = model
        self.memory_manager = memory_manager

        logger.info(
            f"Planner initialized with Gemini {model} "
            f"(memory={'enabled' if memory_manager else 'disabled'})"
        )

    async def generate_plan(
        self,
        job_id: str,
        user_intent: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Generate a structured plan from user intent.

        With memory enabled, injects relevant past experiences into the prompt
        to improve planning quality.

        Args:
            job_id: Unique job identifier
            user_intent: User's task description
            context: Optional context (working_dir, tech_stack, etc.)

        Returns:
            PlanSpec dict with steps, risk levels, artifacts
        """
        logger.info(f"Generating plan for: {user_intent[:50]}...")

        # Retrieve relevant memories if memory manager available
        relevant_memories = []
        if self.memory_manager:
            try:
                # Get task context with embedding for similarity search
                task_context = {"task": user_intent}

                # Generate embedding if vector indexer available
                if hasattr(self.memory_manager, "vector_indexer") and self.memory_manager.vector_indexer:
                    try:
                        embedding = await self.memory_manager.vector_indexer.generate_embedding(
                            user_intent
                        )
                        task_context["embedding"] = embedding
                    except Exception as e:
                        logger.warning(f"Failed to generate embedding: {e}")

                # Retrieve top 5 relevant memories
                memories_with_scores = await self.memory_manager.get_relevant_memories(
                    task_context, limit=5
                )
                relevant_memories = [mem for mem, score in memories_with_scores]

                logger.info(f"Retrieved {len(relevant_memories)} relevant memories")
            except Exception as e:
                logger.warning(f"Failed to retrieve memories: {e}")
                relevant_memories = []

        # Build prompt (with memories if available)
        prompt = self._build_prompt(user_intent, context or {}, relevant_memories)

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

    def _build_prompt(
        self, user_intent: str, context: dict, relevant_memories: list = None
    ) -> str:
        """Build the planning prompt for Gemini."""
        working_dir = context.get("working_dir", "~/projects")

        # Build memory context section
        memory_section = ""
        if relevant_memories:
            memory_section = "\nRELEVANT PAST EXPERIENCES:\n"
            for i, memory in enumerate(relevant_memories[:5], 1):
                content = memory.content
                task = content.get("user_message", content.get("task", "Unknown"))
                success = content.get("success", False)
                status_icon = "[SUCCESS]" if success else "[FAILED]"
                duration = content.get("duration_seconds", 0)

                memory_section += f"\n{i}. {status_icon} Task: {task}\n"

                if success:
                    if "learned_strategy" in content and content["learned_strategy"]:
                        memory_section += f"   Strategy used: {content['learned_strategy']}\n"
                    memory_section += f"   Completed in: {duration}s\n"
                else:
                    error = content.get("error_message", "Unknown error")
                    memory_section += f"   Error: {error}\n"
                    memory_section += "   (Avoid this approach)\n"

            memory_section += "\nUse these experiences to generate a better plan. Learn from failures and replicate successes.\n"

        prompt = f"""You are SKYNET, an autonomous task orchestration AI.

USER REQUEST:
{user_intent}

CONTEXT:
- Working directory: {working_dir}
{memory_section}
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
