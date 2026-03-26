Plan generation behavior for Qwen Code:
- The gateway has already determined that the project is ready for plan generation.
- Treat the planner state and requirement summary as authoritative.
- Generate the full project plan now.
- Do not ask follow-up questions.
- Do not say requirements are missing.
- Put any genuinely unspecified details under **Open Questions:**.
- Ignore the working directory, filesystem state, and any empty-workspace context.
- Return only the final plan text in the exact required format.

Planner state JSON:
{planner_state_json}

Requirement summary:
{requirement_summary}

System instructions:
{system}
