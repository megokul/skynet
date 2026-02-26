<!--
Used as the canonical prompt reference for project_specialist idea-appending flow.
This action is deterministic (no LLM call in current implementation), but we keep
the prompt file so all prompt references resolve to files under prompts/.
-->

When the user message should be treated as an idea for the active project:

1. Append the idea text exactly as given, preserving user intent.
2. Do not switch project scope unless the user explicitly asks to switch.
3. Confirm success with a concise, project-aware response.

