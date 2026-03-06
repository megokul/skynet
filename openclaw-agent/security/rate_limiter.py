"""
CHATHAN Worker — Rate Limiter

Sliding-window rate limiter using an in-memory deque of timestamps.
Thread-safe via an asyncio lock.

When the limit is exceeded the caller receives a ``RateLimitExceeded``
exception which the router translates into a structured rejection.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

from agent_config import RATE_LIMIT_PER_MINUTE


class RateLimitExceeded(Exception):
    """Raised when the per-minute action cap is hit."""

    def __init__(self, limit: int, window_seconds: int = 60):
        """
        Initialize runtime dependencies and object state.
        
        Purpose:
        - Implement `__init__` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `limit`: input used by this function to compute or route work.
        - `window_seconds`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        super().__init__(
            f"Rate limit exceeded: {limit} actions per {window_seconds}s."
        )
        self.limit = limit
        self.window_seconds = window_seconds


class SlidingWindowRateLimiter:
    """
    Allows at most ``max_requests`` actions within a rolling
    ``window_seconds`` window.
    """

    def __init__(
        self,
        max_requests: int = RATE_LIMIT_PER_MINUTE,
        window_seconds: int = 60,
    ):
        """
        Initialize runtime dependencies and object state.
        
        Purpose:
        - Implement `__init__` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `max_requests`: input used by this function to compute or route work.
        - `window_seconds`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        self._max = max_requests
        self._window = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """
        Record a new request timestamp.

        Raises ``RateLimitExceeded`` if the window is full.
        """
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self._window

            # Evict expired entries.
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

            if len(self._timestamps) >= self._max:
                raise RateLimitExceeded(self._max, self._window)

            self._timestamps.append(now)

    async def remaining(self) -> int:
        """Return how many requests are still available in the window."""
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self._window
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            return max(0, self._max - len(self._timestamps))

