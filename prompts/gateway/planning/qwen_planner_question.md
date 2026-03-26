reply_contract: ask_next_question
The gateway has identified missing requirement slots that still need clarification.
Ask only about the listed missing slots. Ask 1-2 concise questions maximum.
Do not ask about slots that are already answered.
Do not repeat a question if the user already answered it explicitly or implicitly.
Do not restart the conversation.
Return only the next assistant reply text.

Conversation status:
- Latest assistant message: {latest_assistant_message}
- Latest user message: {latest_user_message}
- Missing slots: {missing_slots_json}
- Suggested question targets: {question_targets_json}

Planner state JSON:
{planner_state_json}

Requirement summary:
{requirement_summary}

Conversation history JSON:
{conversation_history_json}
