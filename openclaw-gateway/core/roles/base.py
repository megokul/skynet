"""
Base contracts shared by all core roles.

`RoleContext` defines the injected runtime dependencies and `RoleOutput`
defines the engine command protocol returned by each role.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from core.conversation_manager import Conversation, ConversationManager


RoleCommand = Literal["respond", "delegate", "continue", "complete"]


@dataclass(slots=True)
class RoleOutput:
    """
    Normalized contract every role returns to the engine.

    Semantics:
    - `respond`: return assistant text directly; keep current role.
    - `delegate`: switch to `target_role` and execute that role.
    - `continue`: ask follow-up while specialist keeps control.
    - `complete`: specialist task done; engine returns control to commander.
    """

    command: RoleCommand
    response: str | None = None
    target_role: str | None = None
    result: dict[str, Any] | None = None


@dataclass(slots=True)
class RoleContext:
    """
    Dependency/context bundle injected into each role per turn.

    This avoids hidden globals and makes role behavior explicit/testable.
    """

    db: Any
    provider_router: Any
    conversation_manager: ConversationManager
    conversation: Conversation
    project_manager: Any | None = None
    scheduler: Any | None = None
    intent_extractor: Any | None = None


class Role:
    """
    Base role interface.

    Implementations must be side-effect aware and return a `RoleOutput` object
    instead of mutating engine state directly.
    """

    name = "role"

    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
        """
        Handle message.
        
        Purpose:
        - Implement `handle_message` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `context`: input used by this function to compute or route work.
        - `user_text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `RoleOutput` when available; otherwise side effects only.
        """

        raise NotImplementedError
