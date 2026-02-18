"""SKYNET — CI/CD Skill (GitHub Actions workflow generation and monitoring)."""

from __future__ import annotations
from typing import Any
from .base import BaseSkill, SkillContext


# Workflow templates by type and stack.
_WORKFLOW_TEMPLATES: dict[str, dict[str, str]] = {
    "test": {
        "python": """name: Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: python -m pytest --tb=short -q
""",
        "node": """name: Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm ci
      - run: npm test
""",
    },
    "build": {
        "python": """name: Build
on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: python -m build
""",
        "node": """name: Build
on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm ci
      - run: npm run build
""",
    },
    "lint": {
        "python": """name: Lint
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install ruff
      - run: ruff check .
""",
        "node": """name: Lint
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm ci
      - run: npx eslint .
""",
    },
}


class CICDSkill(BaseSkill):
    name = "cicd"
    description = "GitHub Actions CI/CD automation"
    allowed_roles = ["devops", "deployment"]
    requires_approval = {"trigger_github_action"}
    plan_auto_approved = {"generate_github_workflow", "check_github_action_status"}

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "generate_github_workflow",
                "description": (
                    "Generate a GitHub Actions workflow YAML file. "
                    "Writes the file to .github/workflows/ in the project."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "working_dir": {"type": "string", "description": "Project directory"},
                        "workflow_type": {
                            "type": "string",
                            "enum": ["test", "build", "lint"],
                            "description": "Type of workflow to generate",
                        },
                        "tech_stack": {
                            "type": "string",
                            "enum": ["python", "node"],
                            "description": "Technology stack (default: python)",
                        },
                    },
                    "required": ["working_dir", "workflow_type"],
                },
            },
            {
                "name": "check_github_action_status",
                "description": "Check the status of recent GitHub Actions workflow runs.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "working_dir": {"type": "string", "description": "Git repo directory"},
                    },
                    "required": ["working_dir"],
                },
            },
        ]

    async def execute(self, tool_name: str, tool_input: dict[str, Any], context: SkillContext) -> str:
        if tool_name == "generate_github_workflow":
            return await self._generate_workflow(tool_input, context)
        elif tool_name == "check_github_action_status":
            return await context.send_to_agent("gh_run_list", {
                "working_dir": tool_input.get("working_dir", ""),
            })
        return f"Unknown CI/CD tool: {tool_name}"

    async def _generate_workflow(
        self,
        params: dict[str, Any],
        context: SkillContext,
    ) -> str:
        workflow_type = params.get("workflow_type", "test")
        tech_stack = params.get("tech_stack", "python")
        working_dir = params.get("working_dir", context.project_path)

        templates = _WORKFLOW_TEMPLATES.get(workflow_type, {})
        yaml_content = templates.get(tech_stack, templates.get("python", ""))

        if not yaml_content:
            return f"No template for workflow_type={workflow_type}, tech_stack={tech_stack}"

        filepath = f"{working_dir}\\.github\\workflows\\{workflow_type}.yml"
        return await context.send_to_agent("file_write", {
            "file": filepath,
            "content": yaml_content,
        })
