"""Compatibility shim for legacy step-tracing API.

The cognitive trace system is implemented in `core/dev_trace.py`.
This module intentionally performs no trace output and only preserves import
compatibility for existing call sites.
"""

from __future__ import annotations

import contextvars
from typing import Any, Callable


_TRACE_CONTEXT: contextvars.ContextVar["TraceLogger | None"] = contextvars.ContextVar(
    "legacy_trace_logger",
    default=None,
)


class TraceLogger:
    """No-op legacy logger."""

    def __init__(self, **_: Any) -> None:
        pass

    def log_step(self, **_: Any) -> None:
        pass

    def end(self) -> None:
        pass


class TraceManager:
    """No-op lifecycle manager preserving old method signatures."""

    def start(self, **kwargs: Any) -> tuple[TraceLogger, contextvars.Token]:
        logger = TraceLogger(**kwargs)
        token = set_current_trace(logger)
        return logger, token

    def current(self) -> TraceLogger | None:
        return get_current_trace()

    def clear(self, token: contextvars.Token | None = None) -> None:
        clear_current_trace(token)


trace_manager = TraceManager()


def set_current_trace(trace_logger: TraceLogger | None) -> contextvars.Token:
    return _TRACE_CONTEXT.set(trace_logger)


def get_current_trace() -> TraceLogger | None:
    return _TRACE_CONTEXT.get()


def clear_current_trace(token: contextvars.Token | None = None) -> None:
    if token is not None:
        _TRACE_CONTEXT.reset(token)
    else:
        _TRACE_CONTEXT.set(None)


def trace_step(**_: Any) -> None:
    """Legacy no-op step helper."""
    return None


def trace(
    *,
    role: str,
    prompt: str | None = None,
    step_name: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Legacy no-op decorator preserving wrapped function behavior."""
    del role, prompt, step_name

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return func

    return decorator
