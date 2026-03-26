# Prompt Library

Runtime prompts live here instead of inside Python modules.

- Default prompt root: `prompts/`
- Optional override: set `SKYNET_PROMPT_LIBRARY_DIR` to point at another directory
- Inspect the active library with: `python scripts/dev/list_prompts.py`

Layout:

- `ai/`
  Shared AI/system prompts used by gateway AI helpers.
- `gateway/`
  Telegram, planner, coding, and orchestration prompts used by runtime flows.
- `testing/`
  Prompt fixtures and expected prompt-like outputs used by tests and prompt audits.

Guidelines:

- Keep prompts in plain text or Markdown files.
- Prefer placeholders such as `{project_name}` over string assembly in code.
- Put new runtime prompt text here first, then load it with `skynet.prompt_library`.
