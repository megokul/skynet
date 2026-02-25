"""Shared message filtering utilities."""


def strip_tool_messages(messages: list[dict]) -> list[dict]:
    """
    Remove tool_result messages from a conversation history.
    Filters messages where content is a list containing any block with type == 'tool_result'.
    """
    return [
        m for m in messages
        if not (
            isinstance(m.get("content"), list)
            and any(x.get("type") == "tool_result" for x in m["content"])
        )
    ]
