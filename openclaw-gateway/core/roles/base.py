from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from core.conversation_manager import Conversation, ConversationManager


RoleCommand = Literal["respond", "delegate", "continue", "complete"]


@dataclass(slots=True)
class RoleOutput:
    command: RoleCommand
    response: str | None = None
    target_role: str | None = None
    result: dict[str, Any] | None = None


@dataclass(slots=True)
class RoleContext:
    db: Any
    provider_router: Any
    conversation_manager: ConversationManager
    conversation: Conversation
    project_manager: Any | None = None
    scheduler: Any | None = None
    intent_extractor: Any | None = None


class Role:
    name = "role"

    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
        raise NotImplementedError