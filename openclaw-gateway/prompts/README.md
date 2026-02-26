# Prompt Library
Centralized runtime prompts for SKYNET/OpenClaw.

Structure:
- `active/` reusable guidance blocks
- `agents/` generic agent behavioral prompts
- `core/` commander-role prompts
- `bot/` Telegram bot prompts
- `ai/` planner/coder/testing, context, and role prompts
- `orchestrator/` legacy orchestration prompts
- `intents/` deterministic payload extraction prompts

All runtime prompt text should be loaded via `core/prompt_library.py`.
