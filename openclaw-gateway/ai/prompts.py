"""
SKYNET - System prompts loaded from the centralized prompt library.
"""

from __future__ import annotations

from skynet.prompt_library import load_prompt, render_prompt

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

def planning_prompt() -> str:
    return load_prompt("ai/planning_system.md")


def coding_prompt() -> str:
    return load_prompt("ai/coding_system.md")


def testing_prompt() -> str:
    return load_prompt("ai/testing_system.md")


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
        + render_prompt("ai/base_rules.md", project_path=project_path)
    )
    template_ref = _AGENT_PROMPT_FILES.get(role, _AGENT_PROMPT_FILES["backend"])
    return render_prompt(template_ref, base_context=base_context)
