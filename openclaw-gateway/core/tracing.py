"""
Human-readable execution trace framework.

Trace entries are buffered per conversation turn and flushed atomically to
`logs/skynet.trace.log` to keep step narratives contiguous and debuggable.
"""

from __future__ import annotations

import contextvars
import dataclasses
import functools
import inspect
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


_TRACE_FILE = Path(__file__).resolve().parents[1] / "logs" / "skynet.trace.log"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WRITE_LOCK = threading.Lock()
_SEPARATOR = "=" * 80
_STEP_SEPARATOR = "-" * 80
_MAX_TEXT = 1200

_trace_context: contextvars.ContextVar["TraceLogger | None"] = contextvars.ContextVar(
    "skynet_trace_logger",
    default=None,
)


def _utc_now_z() -> str:
    """UTC timestamp in compact Zulu format for trace headers."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_lines(lines: list[str]) -> None:
    """
    Append trace lines to canonical trace file and mirror sinks.

    File write is lock-protected to avoid interleaving across threads.
    """
    _TRACE_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    with _WRITE_LOCK:
        with _TRACE_FILE.open("a", encoding="utf-8") as handle:
            handle.write(text)
    _emit_mirror(text)


def _emit_mirror(text: str) -> None:
    """
    Send trace text to configured logging mirror handlers.

    Mirror failures are intentionally non-fatal because local trace persistence
    is the primary source of truth.
    """
    logger = logging.getLogger("skynet.trace.mirror")
    if not logger.handlers:
        return
    payload = text.replace("\r\n", "\n")
    if not payload.strip():
        return
    try:
        # Emit line-by-line to keep SSH mirror payloads small and reliable.
        for line in payload.split("\n"):
            logger.info("%s", line)
    except Exception:
        # Mirror sinks must never break trace persistence.
        pass


def _clip_text(value: str, *, limit: int = _MAX_TEXT) -> str:
    """Cap long strings to avoid oversized trace payloads."""
    if len(value) <= limit:
        return value
    return value[:limit] + "...<truncated>"


def _sanitize(value: Any) -> Any:
    """Recursive sanitizer used by trace formatter/serializer."""
    if isinstance(value, str):
        return _clip_text(value.replace("\r", " ").replace("\n", "\\n"))
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if dataclasses.is_dataclass(value):
        try:
            return _sanitize(dataclasses.asdict(value))
        except Exception:
            return _clip_text(repr(value))
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(v) for v in value]
    if hasattr(value, "id"):
        return {"id": _sanitize(getattr(value, "id"))}
    if hasattr(value, "__dict__"):
        fields = {}
        for name in ("id", "name", "active_role", "active_project_id", "intent", "command"):
            if hasattr(value, name):
                fields[name] = _sanitize(getattr(value, name))
        if fields:
            return fields
    return _clip_text(repr(value))


def _format_value(value: Any) -> str:
    """Render a sanitized value as trace-friendly text."""
    sanitized = _sanitize(value)
    if isinstance(sanitized, str):
        return json.dumps(sanitized, ensure_ascii=False)
    if isinstance(sanitized, bool):
        return "true" if sanitized else "false"
    if sanitized is None:
        return "null"
    if isinstance(sanitized, (int, float)):
        return str(sanitized)
    return json.dumps(sanitized, ensure_ascii=False, sort_keys=True)


def _format_mapping(title: str, data: dict[str, Any] | None) -> list[str]:
    """Format key/value section blocks (parameters/result/state)."""
    lines = [f"{title}:"]
    if not data:
        lines.append("  (none)")
        return lines
    for key, value in data.items():
        lines.append(f"  {key}: {_format_value(value)}")
    return lines


def _to_repo_relative(path: Path) -> str:
    """Best-effort conversion to repository-relative display path."""
    try:
        return path.resolve().relative_to(_REPO_ROOT).as_posix()
    except Exception:
        return path.resolve().as_posix()


class TraceLogger:
    """
    Human-readable execution trace logger.

    Each instance represents one conversation turn:
    - starts at `TRACE START`
    - accumulates ordered steps in memory
    - flushes atomically at `TRACE END`
    """

    def __init__(
        self,
        *,
        trace_id: str,
        user_id: str | int,
        entrypoint: str,
        input_text: str,
    ) -> None:
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
        - `trace_id`: input used by this function to compute or route work.
        - `user_id`: input used by this function to compute or route work.
        - `entrypoint`: input used by this function to compute or route work.
        - `input_text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        self.trace_id = str(trace_id)
        self.user_id = str(user_id)
        self.entrypoint = entrypoint
        self.input_text = input_text
        self.timestamp = _utc_now_z()
        self._step = 0
        self._start_perf = time.perf_counter()
        self._ended = False
        self._buffer_lock = threading.Lock()
        self._lines: list[str] = []
        self._write_start()

    def _write_start(self) -> None:
        """Initialize trace header in the in-memory buffer."""
        self._append_lines(
            [
                _SEPARATOR,
                "TRACE START",
                f"trace_id: {self.trace_id}",
                f"timestamp: {self.timestamp}",
                f"user_id: {self.user_id}",
                f"entrypoint: {self.entrypoint}",
                f"input: {_format_value(self.input_text)}",
                _SEPARATOR,
                "",
            ]
        )

    def _append_lines(self, lines: list[str]) -> None:
        """Thread-safe append into per-trace line buffer."""
        with self._buffer_lock:
            self._lines.extend(lines)

    def log_step(
        self,
        *,
        function_name: str,
        file_name: str,
        function_path: str | None = None,
        role: str,
        parameters: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        prompt: str | None = None,
        state_before: dict[str, Any] | None = None,
        state_after: dict[str, Any] | None = None,
        execution_time_ms: float | int = 0.0,
    ) -> None:
        """Append one formatted step into this trace."""
        self._step += 1
        lines: list[str] = [
            f"[STEP {self._step}] {function_name}()",
            f"file: {file_name}",
            f"role: {role}",
        ]
        if function_path:
            lines.insert(2, f"path: {function_path}")
        if prompt:
            lines.append(f"prompt: {prompt}")
        lines.append("")
        lines.extend(_format_mapping("parameters", parameters or {}))
        lines.append("")
        if state_before is not None:
            lines.extend(_format_mapping("state_before", state_before))
            lines.append("")
        if state_after is not None:
            lines.extend(_format_mapping("state_after", state_after))
            lines.append("")
        lines.extend(_format_mapping("result", result or {}))
        lines.append("")
        lines.append(f"execution_time: {int(round(float(execution_time_ms)))} ms")
        lines.append("")
        lines.append(_STEP_SEPARATOR)
        lines.append("")
        self._append_lines(lines)

    def end(self) -> None:
        """Close trace and flush entire buffered trace atomically."""
        if self._ended:
            return
        self._ended = True
        total_ms = int(round((time.perf_counter() - self._start_perf) * 1000.0))
        self._append_lines(
            [
                "TRACE END",
                f"trace_id: {self.trace_id}",
                f"total_execution_time: {total_ms} ms",
                _SEPARATOR,
                "",
            ]
        )
        with self._buffer_lock:
            snapshot = list(self._lines)
            self._lines.clear()
        _write_lines(snapshot)


class TraceManager:
    """
    Lightweight trace lifecycle manager backed by contextvars.

    Contextvars keep async task-local trace isolation without global mutable
    cross-talk.
    """

    def start(
        self,
        *,
        trace_id: str,
        user_id: str | int,
        entrypoint: str,
        input_text: str,
    ) -> tuple[TraceLogger, contextvars.Token]:
        """
        Start.
        
        Purpose:
        - Implement `start` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `trace_id`: input used by this function to compute or route work.
        - `user_id`: input used by this function to compute or route work.
        - `entrypoint`: input used by this function to compute or route work.
        - `input_text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `tuple[TraceLogger, contextvars.Token]` when available; otherwise side effects only.
        """

        logger = TraceLogger(
            trace_id=trace_id,
            user_id=user_id,
            entrypoint=entrypoint,
            input_text=input_text,
        )
        token = set_current_trace(logger)
        return logger, token

    def current(self) -> TraceLogger | None:
        """
        Current.
        
        Purpose:
        - Implement `current` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `TraceLogger | None` when available; otherwise side effects only.
        """

        return get_current_trace()

    def clear(self, token: contextvars.Token | None = None) -> None:
        """
        Clear.
        
        Purpose:
        - Implement `clear` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `token`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        clear_current_trace(token)


trace_manager = TraceManager()


def set_current_trace(trace_logger: TraceLogger | None) -> contextvars.Token:
    """Bind current trace in task-local context."""
    return _trace_context.set(trace_logger)


def get_current_trace() -> TraceLogger | None:
    """Get current task-local trace logger, if any."""
    return _trace_context.get()


def clear_current_trace(token: contextvars.Token | None = None) -> None:
    """Clear/reset current task-local trace binding."""
    if token is not None:
        _trace_context.reset(token)
    else:
        _trace_context.set(None)


def trace_step(
    *,
    function_name: str,
    file_name: str,
    function_path: str | None = None,
    role: str,
    parameters: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    prompt: str | None = None,
    state_before: dict[str, Any] | None = None,
    state_after: dict[str, Any] | None = None,
    execution_time_ms: float | int = 0.0,
) -> None:
    """Manual trace-step helper for non-decorated code paths."""
    current = get_current_trace()
    if current is None:
        return
    current.log_step(
        function_name=function_name,
        file_name=file_name,
        function_path=function_path,
        role=role,
        parameters=parameters,
        result=result,
        prompt=prompt,
        state_before=state_before,
        state_after=state_after,
        execution_time_ms=execution_time_ms,
    )


def _extract_parameters(
    signature: inspect.Signature,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Extract call arguments while hiding `self/cls`."""
    try:
        bound = signature.bind_partial(*args, **kwargs)
    except Exception:
        return {"args": _sanitize(args), "kwargs": _sanitize(kwargs)}

    values: dict[str, Any] = {}
    for name, value in bound.arguments.items():
        if name in {"self", "cls"}:
            continue
        values[name] = _sanitize(value)
    return values


def _result_payload(value: Any) -> dict[str, Any]:
    """Normalize arbitrary return value into mapping form."""
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if dataclasses.is_dataclass(value):
        try:
            return {str(k): _sanitize(v) for k, v in dataclasses.asdict(value).items()}
        except Exception:
            return {"value": _sanitize(value)}
    return {"value": _sanitize(value)}


def trace(
    *,
    role: str,
    prompt: str | None = None,
    step_name: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator for automatic trace step logging.

    Supports sync and async functions. On exceptions, logs failure result block
    before re-raising.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        """
        Decorator.
        
        Purpose:
        - Implement `decorator` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `func`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `Callable[..., Any]` when available; otherwise side effects only.
        """

        signature = inspect.signature(func)
        file_path = Path(func.__code__.co_filename)
        file_name = file_path.name
        function_path = _to_repo_relative(file_path)
        traced_name = (step_name or func.__name__).strip() or func.__name__

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                """
                Async wrapper.
                
                Purpose:
                - Implement `async_wrapper` within this module's workflow.
                - Keep behavior localized so callers have one stable entrypoint.
                
                How it works:
                - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
                - Produces deterministic return data or side effects expected by calling code.
                
                Why this exists:
                - Prevents duplicated logic in upstream orchestration paths.
                - Improves debuggability by centralizing this behavior in one named function.
                
                Parameters:
                - `*args`: input used by this function to compute or route work.
                - `**kwargs`: input used by this function to compute or route work.
                
                Returns:
                - Return value typed as `Any` when available; otherwise side effects only.
                """

                current = get_current_trace()
                if current is None:
                    return await func(*args, **kwargs)

                params = _extract_parameters(signature, args, kwargs)
                started = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:
                    current.log_step(
                        function_name=traced_name,
                        file_name=file_name,
                        function_path=function_path,
                        role=role,
                        prompt=prompt,
                        parameters=params,
                        result={"success": False, "error": _sanitize(str(exc))},
                        execution_time_ms=(time.perf_counter() - started) * 1000.0,
                    )
                    raise

                current.log_step(
                    function_name=traced_name,
                    file_name=file_name,
                    function_path=function_path,
                    role=role,
                    prompt=prompt,
                    parameters=params,
                    result=_result_payload(result),
                    execution_time_ms=(time.perf_counter() - started) * 1000.0,
                )
                return result

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            """
            Sync wrapper.
            
            Purpose:
            - Implement `sync_wrapper` within this module's workflow.
            - Keep behavior localized so callers have one stable entrypoint.
            
            How it works:
            - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
            - Produces deterministic return data or side effects expected by calling code.
            
            Why this exists:
            - Prevents duplicated logic in upstream orchestration paths.
            - Improves debuggability by centralizing this behavior in one named function.
            
            Parameters:
            - `*args`: input used by this function to compute or route work.
            - `**kwargs`: input used by this function to compute or route work.
            
            Returns:
            - Return value typed as `Any` when available; otherwise side effects only.
            """

            current = get_current_trace()
            if current is None:
                return func(*args, **kwargs)

            params = _extract_parameters(signature, args, kwargs)
            started = time.perf_counter()
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                current.log_step(
                    function_name=traced_name,
                    file_name=file_name,
                    function_path=function_path,
                    role=role,
                    prompt=prompt,
                    parameters=params,
                    result={"success": False, "error": _sanitize(str(exc))},
                    execution_time_ms=(time.perf_counter() - started) * 1000.0,
                )
                raise

            current.log_step(
                function_name=traced_name,
                file_name=file_name,
                function_path=function_path,
                role=role,
                prompt=prompt,
                parameters=params,
                result=_result_payload(result),
                execution_time_ms=(time.perf_counter() - started) * 1000.0,
            )
            return result

        return sync_wrapper

    return decorator
