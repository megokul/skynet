"""
SKYNET - System prompts loaded from the centralized prompt library.
"""

from __future__ import annotations

from core.prompt_library import load_prompt

PLANNING_PROMPT = load_prompt("ai/planning_system.md")
CODING_PROMPT = load_prompt("ai/coding_system.md")
TESTING_PROMPT = load_prompt("ai/testing_system.md")
_BASE_RULES = load_prompt("ai/base_rules.md")

_AGENT_PROMPT_FILES: dict[str, str] = {
    "architect": "ai/roles/architect.md",
    "backend": "ai/roles/backend.md",
    "frontend": "ai/roles/frontend.md",
    "api": "ai/roles/api.md",
    "testing": "ai/roles/testing.md",
    "debug": "ai/roles/debug.md",
    "devops": "ai/roles/devops.md",
    "research": "ai/roles/research.md",
    "optimization": "ai/roles/optimization.md",
    "deployment": "ai/roles/deployment.md",
    "monitoring": "ai/roles/monitoring.md",
}

AGENT_PROMPTS: dict[str, str] = {
    role: load_prompt(path)
    for role, path in _AGENT_PROMPT_FILES.items()
}


def get_agent_prompt(
    role: str,
    project_name: str,
    project_description: str,
    tech_stack: str,
    current_milestone: str,
    current_task: str,
    project_path: str,
) -> str:
    """Build a role-specific system prompt for a specialized agent."""
    base_context = (
        f"Project: {project_name}\n"
        f"Description: {project_description}\n"
        f"Tech Stack: {tech_stack}\n"
        f"Current milestone: {current_milestone}\n"
        f"Current task: {current_task}\n"
        f"Project directory: {project_path}\n"
        + _BASE_RULES.format(project_path=project_path)
    )
    template = AGENT_PROMPTS.get(role, AGENT_PROMPTS["backend"])
    return template.format(base_context=base_context)
