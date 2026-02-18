"""
SKYNET — Agent Role Definitions

Configuration data for each specialized agent role. Each role has
a persona, preferred AI providers, skill set, and operational limits.
"""

from __future__ import annotations

from typing import Any


AGENT_CONFIGS: dict[str, dict[str, Any]] = {
    "architect": {
        "display_name": "Architect Agent",
        "description": "Designs system architecture, module boundaries, and project structure",
        "preferred_providers": ["gemini", "claude"],
        "default_task_type": "planning",
        "skills": ["filesystem", "search", "git"],
        "max_tool_rounds": 10,
    },
    "backend": {
        "display_name": "Backend Agent",
        "description": "Implements server-side code, APIs, databases, and business logic",
        "preferred_providers": ["ollama", "groq"],
        "default_task_type": "general",
        "skills": ["filesystem", "git", "build", "search", "docker"],
        "max_tool_rounds": 30,
    },
    "frontend": {
        "display_name": "Frontend Agent",
        "description": "Implements UI components, styling, and client-side logic",
        "preferred_providers": ["ollama", "groq"],
        "default_task_type": "general",
        "skills": ["filesystem", "git", "build", "search", "ide"],
        "max_tool_rounds": 30,
    },
    "api": {
        "display_name": "API Agent",
        "description": "Designs and implements REST/GraphQL APIs and integrations",
        "preferred_providers": ["ollama", "groq"],
        "default_task_type": "crud",
        "skills": ["filesystem", "git", "build", "search"],
        "max_tool_rounds": 25,
    },
    "testing": {
        "display_name": "Testing Agent",
        "description": "Writes and runs tests, validates code quality and coverage",
        "preferred_providers": ["groq", "ollama"],
        "default_task_type": "unit_test",
        "skills": ["filesystem", "git", "build", "search"],
        "max_tool_rounds": 20,
    },
    "debug": {
        "display_name": "Debug Agent",
        "description": "Diagnoses bugs, analyzes error logs, performs root cause analysis",
        "preferred_providers": ["deepseek", "claude"],
        "default_task_type": "hard_debug",
        "skills": ["filesystem", "git", "build", "search"],
        "max_tool_rounds": 25,
    },
    "devops": {
        "display_name": "DevOps Agent",
        "description": "Docker, CI/CD pipelines, deployment, infrastructure",
        "preferred_providers": ["ollama", "groq"],
        "default_task_type": "general",
        "skills": ["filesystem", "git", "docker", "cicd", "build"],
        "max_tool_rounds": 20,
    },
    "research": {
        "display_name": "Research Agent",
        "description": "Researches technologies, libraries, and best practices",
        "preferred_providers": ["gemini"],
        "default_task_type": "planning",
        "skills": ["search", "filesystem"],
        "max_tool_rounds": 15,
    },
    "optimization": {
        "display_name": "Optimization Agent",
        "description": "Performance tuning, code optimization, bundle size reduction",
        "preferred_providers": ["deepseek", "claude"],
        "default_task_type": "complex_refactor",
        "skills": ["filesystem", "git", "build", "search"],
        "max_tool_rounds": 20,
    },
    "deployment": {
        "display_name": "Deployment Agent",
        "description": "Deploys applications, manages releases, monitors deployments",
        "preferred_providers": ["ollama", "groq"],
        "default_task_type": "general",
        "skills": ["filesystem", "git", "docker", "cicd", "build"],
        "max_tool_rounds": 15,
    },
    "monitoring": {
        "display_name": "Monitoring Agent",
        "description": "Sets up logging, monitoring, health checks, and alerting",
        "preferred_providers": ["ollama", "groq"],
        "default_task_type": "general",
        "skills": ["filesystem", "git", "build", "search"],
        "max_tool_rounds": 15,
    },
}


# All valid role names.
ALL_ROLES = set(AGENT_CONFIGS.keys())

# Default role for tasks that don't have an explicit assignment.
DEFAULT_ROLE = "backend"
