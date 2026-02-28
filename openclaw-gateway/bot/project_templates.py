"""
SKYNET — Project Specialist Templates

One template per project type. Each template provides:
  - stack     : recommended default tech stack
  - questions : ordered list of key requirements questions
  - structure : top-level folder layout hint
  - scaffold  : shell commands to bootstrap the project on the worker laptop

The Project Specialist uses these to ask the right questions fast and
generate a well-structured plan aligned with the project type.
"""
from __future__ import annotations

TEMPLATES: dict[str, dict] = {
    "Web App": {
        "stack": "Next.js / React + Tailwind CSS + PostgreSQL",
        "questions": [
            "What is the main purpose of this site? (e-commerce, SaaS, portfolio, other)",
            "Who are the target users?",
            "Do you need user authentication / accounts?",
            "Which pages or features should we build first?",
            "Preferred hosting? (Vercel, EC2, other)",
        ],
        "structure": "src/ components/ pages/ api/ public/ tests/",
        "scaffold": [
            "npx create-next-app@latest . --typescript --tailwind --eslint --app",
        ],
    },

    "Mobile": {
        "stack": "React Native + Expo + TypeScript",
        "questions": [
            "iOS, Android, or both?",
            "What are the main screens / features?",
            "Does it need a backend API or cloud sync?",
            "Any specific native features? (camera, GPS, push notifications)",
        ],
        "structure": "src/ components/ screens/ navigation/ assets/ tests/",
        "scaffold": [
            "npx create-expo-app . --template expo-template-blank-typescript",
        ],
    },

    "CLI": {
        "stack": "Python 3.11+ + Typer + Rich",
        "questions": [
            "What does this CLI tool do in one sentence?",
            "What are the main commands / subcommands?",
            "Does it need config files or persistent state?",
            "Should it be installable via pip?",
        ],
        "structure": "src/ tests/ docs/",
        "scaffold": [
            "pip install typer rich pytest",
            "mkdir -p src tests docs",
        ],
    },

    "Library": {
        "stack": "Python package (PyPI) or npm package",
        "questions": [
            "What does this library do?",
            "What language / runtime? (Python, Node.js, other)",
            "Does it wrap or extend any existing library?",
            "Who is the target developer audience?",
        ],
        "structure": "src/ tests/ docs/ examples/",
        "scaffold": [],
    },

    "Bot": {
        "stack": "Python + python-telegram-bot or discord.py",
        "questions": [
            "Which platform? (Telegram, Discord, Slack, other)",
            "What will the bot do?",
            "Does it need a database or persistent state?",
            "Any AI / LLM features?",
        ],
        "structure": "bot/ handlers/ db/ tests/",
        "scaffold": [],
    },

    "Other": {
        "stack": "To be determined based on requirements",
        "questions": [
            "What are you building? Describe it in 2-3 sentences.",
            "What language or runtime do you prefer?",
            "What is the main deliverable? (executable, API, service, script)",
            "Any constraints or existing systems to integrate with?",
        ],
        "structure": "src/ tests/",
        "scaffold": [],
    },
}


def get_template(project_type: str) -> dict:
    """Return the template for the given project type, falling back to Other."""
    return TEMPLATES.get(project_type, TEMPLATES["Other"])
