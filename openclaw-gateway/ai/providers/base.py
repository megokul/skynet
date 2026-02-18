"""
SKYNET — Abstract AI Provider

Every AI provider adapter inherits from ``BaseProvider`` and implements
the ``chat()`` method.  The provider router uses the common interface to
switch transparently between Ollama, Gemini, Groq, Claude, OpenAI, etc.
"""

from __future__ import annotations

import dataclasses
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any


@dataclasses.dataclass
class ToolCall:
    """A single tool invocation requested by the model."""
    id: str
    name: str
    input: dict[str, Any]


@dataclasses.dataclass
class ProviderResponse:
    """Normalised response from any provider."""
    text: str = ""
    tool_calls: list[ToolCall] = dataclasses.field(default_factory=list)
    stop_reason: str = "end_turn"  # "end_turn" | "tool_use" | "max_tokens"
    input_tokens: int = 0
    output_tokens: int = 0
    provider_name: str = ""
    model: str = ""
    raw: Any = None  # original response object for debugging


@dataclasses.dataclass
class QuotaInfo:
    """Current quota status for a provider."""
    daily_limit: int | None  # None = unlimited
    daily_used: int
    rpm_limit: int | None
    rpm_used: int
    resets_at: datetime | None  # next quota reset time


class BaseProvider(ABC):
    """Abstract base class for AI provider adapters."""

    # --- Identity ---
    name: str = "base"

    # --- Capabilities ---
    supports_tool_use: bool = False
    supports_json_strict: bool = False
    context_limit: int = 128_000      # max input tokens
    cost_rank: int = 99               # lower = cheaper (0 = free local)

    # --- Quota ---
    daily_limit: int | None = None
    rpm_limit: int | None = None

    def __init__(self, api_key: str, model: str | None = None):
        self.api_key = api_key
        self.model_name = model or self.default_model
        self._daily_used: int = 0
        self._daily_date: str = ""
        self._rpm_timestamps: list[float] = []

        # --- Error tracking & cooldown ---
        self.cooldown_until: float = 0.0
        self._consecutive_errors: int = 0
        self._error_deprioritize_until: float = 0.0

    @property
    @abstractmethod
    def default_model(self) -> str:
        ...

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> ProviderResponse:
        """Send a chat completion request and return a normalised response."""
        ...

    # ------------------------------------------------------------------
    # Quota management
    # ------------------------------------------------------------------

    def remaining_quota(self) -> QuotaInfo:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_date != today:
            self._daily_used = 0
            self._daily_date = today

        return QuotaInfo(
            daily_limit=self.daily_limit,
            daily_used=self._daily_used,
            rpm_limit=self.rpm_limit,
            rpm_used=self._count_recent_rpm(),
            resets_at=None,
        )

    def record_usage(self, tokens: int = 0) -> None:
        """Called by the router after a successful request."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_date != today:
            self._daily_used = 0
            self._daily_date = today
        self._daily_used += 1
        self._rpm_timestamps.append(time.monotonic())
        # Clear error state on success.
        self._consecutive_errors = 0

    def has_quota(self) -> bool:
        """Check if provider has remaining free-tier quota."""
        quota = self.remaining_quota()
        if quota.daily_limit is not None and quota.daily_used >= quota.daily_limit:
            return False
        if quota.rpm_limit is not None and quota.rpm_used >= quota.rpm_limit:
            return False
        return True

    def _count_recent_rpm(self) -> int:
        cutoff = time.monotonic() - 60
        self._rpm_timestamps = [t for t in self._rpm_timestamps if t > cutoff]
        return len(self._rpm_timestamps)

    def load_usage_from_db(self, requests_used: int, date: str) -> None:
        """Restore usage counters from the database after a restart."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if date == today:
            self._daily_used = requests_used
            self._daily_date = today

    # ------------------------------------------------------------------
    # Error tracking & cooldown
    # ------------------------------------------------------------------

    def enter_cooldown(self, seconds: int = 300) -> None:
        """Enter cooldown after repeated rate limit hits (429s)."""
        self.cooldown_until = time.monotonic() + seconds

    def is_in_cooldown(self) -> bool:
        """Check if the provider is in a cooldown period."""
        return time.monotonic() < self.cooldown_until

    def record_error(self) -> None:
        """Record a consecutive error. After 3+, deprioritize for 1 hour."""
        self._consecutive_errors += 1
        if self._consecutive_errors >= 3:
            self._error_deprioritize_until = time.monotonic() + 3600

    def is_deprioritized(self) -> bool:
        """Check if the provider is deprioritized due to high error rate."""
        return time.monotonic() < self._error_deprioritize_until
