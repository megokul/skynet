Produce the next assistant reply for the in-progress Telegram conversation below.
Treat the System instructions block as the authoritative behavior contract.
Respond to the latest user message now.
Ignore the working directory, filesystem state, and any empty-workspace context. It is irrelevant for planner_chat.
Do not describe your role.
Do not say you are ready to assist.
Do not ask what the user wants to work on.
Do not mention the workspace, files, or project directory.
Do not restart the conversation.
If the final user message already requests the full project plan, generate it immediately in the exact required format.
If the latest user message answers the previous assistant question, continue to the next unanswered requirement from the System instructions.
Otherwise, either ask 1-2 concrete follow-up questions grounded in the conversation history or, if enough information is already available, reply with the exact completion sentence required by the System instructions.
Return only the assistant reply text.

Bad responses that are always invalid:
- Understood. I'm ready to assist...
- What would you like to work on?

System instructions:
{system}

Previous assistant message:
{previous_assistant_message}

Latest user message:
{latest_user_message}

Conversation transcript:
{transcript}

Conversation history JSON:
{conversation_history_json}
