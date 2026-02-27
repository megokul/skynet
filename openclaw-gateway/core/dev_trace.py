"""
Development-focused cognitive trace system for orchestration boundaries.

This module buffers one trace per conversation turn and appends the rendered
trace block to `logs/skynet.trace.log` only at conversation end.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
import inspect
import json
from pathlib import Path
import threading
import time
from typing import Any


_TRACE_FILE = Path(__file__).resolve().parents[1] / "logs" / "skynet.trace.log"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WRITE_LOCK = threading.Lock()
_TRACE_CONTEXT: contextvars.ContextVar["DevTraceSession | None"] = contextvars.ContextVar(
    "skynet_dev_trace_session",
    default=None,
)

_SEPARATOR = "=" * 80
_PHASE_SEPARATOR = "-" * 80
_MAX_TEXT = 2000


class DevTracePhase(IntEnum):
    """Fixed cognitive trace phases in required render order."""

    ENTRY = 1
    INTENT = 2
    ROUTING = 3
    SPECIALIST = 4
    RESTORATION = 5
    RESPONSE = 6

    @property
    def label(self) -> str:
        if self is DevTracePhase.ENTRY:
            return "Entry & Normalisation"
        if self is DevTracePhase.INTENT:
            return "Intent Resolution"
        if self is DevTracePhase.ROUTING:
            return "Role Routing"
        if self is DevTracePhase.SPECIALIST:
            return "Specialist Execution"
        if self is DevTracePhase.RESTORATION:
            return "Role Restoration"
        return "Response Construction"


@dataclass(slots=True)
class DevTraceNode:
    """Control-flow node used for tree rendering."""

    node_id: int
    file: str
    line: int
    function: str
    parent_id: int | None = None
    children: list["DevTraceNode"] = field(default_factory=list)


@dataclass(slots=True)
class _DataFlow:
    source_name: str
    source_value: Any
    target_name: str
    target_value: Any


@dataclass(slots=True)
class _StateMutation:
    key: str
    old_value: Any
    new_value: Any


@dataclass(slots=True)
class _OutputEntry:
    key: str
    value: Any


@dataclass(slots=True)
class _RoleEnter:
    role: str


@dataclass(slots=True)
class _RoleSwitch:
    from_role: str
    to_role: str


@dataclass(slots=True)
class _PhaseBuffer:
    phase: DevTracePhase
    roots: list[DevTraceNode] = field(default_factory=list)
    node_index: dict[int, DevTraceNode] = field(default_factory=dict)
    data_flow: list[_DataFlow] = field(default_factory=list)
    decisions: list[Any] = field(default_factory=list)
    state_mutations: list[_StateMutation] = field(default_factory=list)
    outputs: list[_OutputEntry] = field(default_factory=list)
    role_events: list[_RoleEnter | _RoleSwitch] = field(default_factory=list)


class DevTraceSession:
    """In-memory cognitive trace session for one conversation turn."""

    def __init__(
        self,
        *,
        trace_id: str,
        user_id: str | int,
        user_input: str,
        timestamp: str | None = None,
    ) -> None:
        self.trace_id = str(trace_id)
        self.user_id = str(user_id)
        self.user_input = user_input or ""
        self.timestamp = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._lock = threading.RLock()
        self._start_perf = time.perf_counter()
        self._ended = False
        self._next_node_id = 1
        self._decision_points = 0
        self._state_mutation_counts: dict[str, int] = {}
        self._role_chain: list[str] = []
        self._phases: dict[DevTracePhase, _PhaseBuffer] = {
            phase: _PhaseBuffer(phase=phase) for phase in DevTracePhase
        }

    def _phase(self, phase: DevTracePhase | int) -> _PhaseBuffer:
        resolved = DevTracePhase(int(phase))
        return self._phases[resolved]

    def record_control_flow(
        self,
        phase: DevTracePhase | int,
        *,
        file: str | None = None,
        line: int | None = None,
        function: str | None = None,
        parent_id: int | None = None,
        stack_depth: int = 1,
    ) -> int:
        """Record one control-flow node (call-site preferred)."""
        with self._lock:
            p = self._phase(phase)
            if not file or not line or not function:
                captured_file, captured_line, captured_function = _capture_callsite(stack_depth=stack_depth + 1)
                file = file or captured_file
                line = line or captured_line
                function = function or captured_function

            node_id = self._next_node_id
            self._next_node_id += 1
            node = DevTraceNode(
                node_id=node_id,
                file=str(file),
                line=int(line),
                function=str(function),
                parent_id=parent_id,
            )
            p.node_index[node_id] = node
            if parent_id and parent_id in p.node_index:
                p.node_index[parent_id].children.append(node)
            else:
                p.roots.append(node)
            return node_id

    def record_data_flow(
        self,
        phase: DevTracePhase | int,
        *,
        source_name: str,
        source_value: Any,
        target_name: str,
        target_value: Any,
    ) -> None:
        """Record one data transformation event."""
        if _stable_dump(source_value) == _stable_dump(target_value):
            return
        with self._lock:
            self._phase(phase).data_flow.append(
                _DataFlow(
                    source_name=str(source_name),
                    source_value=_sanitize(source_value),
                    target_name=str(target_name),
                    target_value=_sanitize(target_value),
                )
            )

    def record_decision(self, phase: DevTracePhase | int, reasoning: Any) -> None:
        """Record one decision reasoning block."""
        with self._lock:
            self._phase(phase).decisions.append(_sanitize(reasoning))
            self._decision_points += 1

    def record_state_mutation(
        self,
        phase: DevTracePhase | int,
        *,
        key: str,
        old_value: Any,
        new_value: Any,
    ) -> None:
        """Record one state mutation only when value changed."""
        old_s = _sanitize(old_value)
        new_s = _sanitize(new_value)
        if _stable_dump(old_s) == _stable_dump(new_s):
            return
        key_text = str(key)
        with self._lock:
            self._phase(phase).state_mutations.append(
                _StateMutation(
                    key=key_text,
                    old_value=old_s,
                    new_value=new_s,
                )
            )
            self._state_mutation_counts[key_text] = self._state_mutation_counts.get(key_text, 0) + 1

    def record_output(self, phase: DevTracePhase | int, *, key: str, value: Any) -> None:
        """Record one output key/value entry."""
        with self._lock:
            self._phase(phase).outputs.append(_OutputEntry(key=str(key), value=_sanitize(value)))

    def record_role_enter(self, phase: DevTracePhase | int, role: str) -> None:
        """Record role enter marker and update role chain."""
        role_name = (role or "").strip()
        if not role_name:
            return
        with self._lock:
            self._phase(phase).role_events.append(_RoleEnter(role=role_name))
            if not self._role_chain or self._role_chain[-1] != role_name:
                self._role_chain.append(role_name)

    def record_role_switch(self, phase: DevTracePhase | int, *, from_role: str, to_role: str) -> None:
        """Record role switch marker and update role chain."""
        from_name = (from_role or "").strip() or "unknown"
        to_name = (to_role or "").strip() or "unknown"
        with self._lock:
            self._phase(phase).role_events.append(_RoleSwitch(from_role=from_name, to_role=to_name))
            if not self._role_chain:
                self._role_chain.append(from_name)
            elif self._role_chain[-1] != from_name:
                self._role_chain.append(from_name)
            if self._role_chain[-1] != to_name:
                self._role_chain.append(to_name)

    def end(self) -> None:
        """Render and append trace block once."""
        with self._lock:
            if self._ended:
                return
            self._ended = True
            text = self.render(total_execution_ms=int(round((time.perf_counter() - self._start_perf) * 1000.0)))
        _append_trace_block(text)

    def render(self, *, total_execution_ms: int) -> str:
        """Render trace exactly in required human-readable structure."""
        lines: list[str] = [
            _SEPARATOR,
            f"TRACE {self.trace_id}",
            f"{self.timestamp} | user: {self.user_id}",
            "USER INPUT:",
            f"  {json.dumps(self.user_input, ensure_ascii=False)}",
            _SEPARATOR,
            "",
        ]

        for phase in DevTracePhase:
            lines.append(f"PHASE {phase.value} — {phase.label}")
            lines.append(_PHASE_SEPARATOR)
            lines.extend(self._render_phase(self._phase(phase)))
            lines.append("")

        lines.append(_PHASE_SEPARATOR)
        lines.append("TRACE SUMMARY")
        lines.append(_PHASE_SEPARATOR)
        lines.append(f"Total Execution Time: {int(total_execution_ms)} ms")
        lines.append("")
        lines.append("Role Transitions:")
        if self._role_chain:
            lines.append(f"  {' → '.join(self._role_chain)}")
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append("Decision Points:")
        lines.append(f"  {self._decision_points}")
        lines.append("")
        lines.append("State Mutations:")
        if self._state_mutation_counts:
            summary = ", ".join(
                f"{key} ({count})"
                for key, count in sorted(self._state_mutation_counts.items(), key=lambda item: item[0])
            )
            lines.append(f"  {summary}")
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append(_SEPARATOR)
        lines.append("END TRACE")
        lines.append(_SEPARATOR)
        return "\n".join(lines) + "\n"

    def _render_phase(self, phase: _PhaseBuffer) -> list[str]:
        lines: list[str] = []
        lines.append("CONTROL FLOW")
        if phase.roots:
            lines.extend(_render_control_tree(phase.roots))
        else:
            lines.append("└── (none)")
        lines.append("")

        lines.append("DATA FLOW")
        if phase.data_flow:
            for entry in phase.data_flow:
                lines.append(f"  {entry.source_name}")
                lines.append(f"    = {_format_value(entry.source_value)}")
                lines.append(f"    → {entry.target_name}")
                lines.append(f"    = {_format_value(entry.target_value)}")
        else:
            lines.append("  (none)")
        lines.append("")

        lines.append("DECISION REASONING")
        if not phase.role_events and not phase.decisions:
            lines.append("  (none)")
        else:
            for role_event in phase.role_events:
                if isinstance(role_event, _RoleEnter):
                    lines.append(f"  [ROLE ENTER] {role_event.role}")
                else:
                    lines.append("  [ROLE SWITCH]")
                    lines.append(f"    from: {role_event.from_role}")
                    lines.append(f"    to: {role_event.to_role}")
            for decision in phase.decisions:
                lines.extend(_render_structured(decision, indent=2))
        lines.append("")

        lines.append("STATE MUTATION")
        if phase.state_mutations:
            for mutation in phase.state_mutations:
                lines.append(f"  {mutation.key}:")
                lines.append(f"    {_format_value(mutation.old_value)} → {_format_value(mutation.new_value)}")
        else:
            lines.append("  (none)")
        lines.append("")

        lines.append("OUTPUT")
        if phase.outputs:
            for output in phase.outputs:
                lines.append(f"  {output.key} = {_format_value(output.value)}")
        else:
            lines.append("  (none)")
        return lines


def start_trace_session(
    *,
    trace_id: str,
    user_id: str | int,
    user_input: str,
) -> tuple[DevTraceSession, contextvars.Token]:
    """Create and bind a session in task-local context."""
    session = DevTraceSession(trace_id=trace_id, user_id=user_id, user_input=user_input)
    token = _TRACE_CONTEXT.set(session)
    return session, token


def get_current_trace_session() -> DevTraceSession | None:
    """Get current task-local trace session."""
    return _TRACE_CONTEXT.get()


def clear_trace_session(token: contextvars.Token | None = None) -> None:
    """Clear trace session binding."""
    if token is not None:
        _TRACE_CONTEXT.reset(token)
        return
    _TRACE_CONTEXT.set(None)


def trace_control_flow(
    phase: DevTracePhase | int,
    *,
    file: str | None = None,
    line: int | None = None,
    function: str | None = None,
    parent_id: int | None = None,
    stack_depth: int = 1,
) -> int | None:
    """Convenience wrapper around `record_control_flow`."""
    session = get_current_trace_session()
    if session is None:
        return None
    return session.record_control_flow(
        phase,
        file=file,
        line=line,
        function=function,
        parent_id=parent_id,
        stack_depth=stack_depth + 1,
    )


def trace_data_flow(
    phase: DevTracePhase | int,
    *,
    source_name: str,
    source_value: Any,
    target_name: str,
    target_value: Any,
) -> None:
    """Convenience wrapper around `record_data_flow`."""
    session = get_current_trace_session()
    if session is None:
        return
    session.record_data_flow(
        phase,
        source_name=source_name,
        source_value=source_value,
        target_name=target_name,
        target_value=target_value,
    )


def trace_decision(phase: DevTracePhase | int, reasoning: Any) -> None:
    """Convenience wrapper around `record_decision`."""
    session = get_current_trace_session()
    if session is None:
        return
    session.record_decision(phase, reasoning)


def trace_state_mutation(
    phase: DevTracePhase | int,
    *,
    key: str,
    old_value: Any,
    new_value: Any,
) -> None:
    """Convenience wrapper around `record_state_mutation`."""
    session = get_current_trace_session()
    if session is None:
        return
    session.record_state_mutation(
        phase,
        key=key,
        old_value=old_value,
        new_value=new_value,
    )


def trace_output(phase: DevTracePhase | int, *, key: str, value: Any) -> None:
    """Convenience wrapper around `record_output`."""
    session = get_current_trace_session()
    if session is None:
        return
    session.record_output(phase, key=key, value=value)


def trace_role_enter(phase: DevTracePhase | int, role: str) -> None:
    """Convenience wrapper around `record_role_enter`."""
    session = get_current_trace_session()
    if session is None:
        return
    session.record_role_enter(phase, role)


def trace_role_switch(phase: DevTracePhase | int, *, from_role: str, to_role: str) -> None:
    """Convenience wrapper around `record_role_switch`."""
    session = get_current_trace_session()
    if session is None:
        return
    session.record_role_switch(phase, from_role=from_role, to_role=to_role)


def _append_trace_block(text: str) -> None:
    _TRACE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        with _TRACE_FILE.open("a", encoding="utf-8") as handle:
            handle.write(text)


def _to_repo_relative(path: str) -> str:
    try:
        return Path(path).resolve().relative_to(_REPO_ROOT).as_posix()
    except Exception:
        return Path(path).as_posix()


def _capture_callsite(*, stack_depth: int = 1) -> tuple[str, int, str]:
    frame = inspect.currentframe()
    try:
        walker = frame
        # +1 includes this helper frame.
        for _ in range(stack_depth + 1):
            if walker is None:
                break
            walker = walker.f_back
        if walker is None:
            return ("unknown", 0, "unknown")
        info = inspect.getframeinfo(walker)
        return (_to_repo_relative(info.filename), int(info.lineno), str(walker.f_code.co_name))
    except Exception:
        return ("unknown", 0, "unknown")
    finally:
        del frame


def _render_control_tree(nodes: list[DevTraceNode], *, prefix: str = "") -> list[str]:
    lines: list[str] = []
    last_index = len(nodes) - 1
    for index, node in enumerate(nodes):
        is_last = index == last_index
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{node.file}:{node.line}::{node.function}(...)")
        child_prefix = f"{prefix}{'    ' if is_last else '│   '}"
        if node.children:
            lines.extend(_render_control_tree(node.children, prefix=child_prefix))
    return lines


def _render_structured(value: Any, *, indent: int) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{prefix}(none)"]
        lines: list[str] = []
        for key, nested in value.items():
            if isinstance(nested, dict):
                lines.append(f"{prefix}{key}:")
                lines.extend(_render_structured(nested, indent=indent + 2))
            elif isinstance(nested, list):
                lines.append(f"{prefix}{key}:")
                if not nested:
                    lines.append(f"{prefix}  (none)")
                else:
                    for item in nested:
                        if isinstance(item, (dict, list)):
                            lines.append(f"{prefix}  -")
                            lines.extend(_render_structured(item, indent=indent + 4))
                        else:
                            lines.append(f"{prefix}  - {_format_value(item)}")
            else:
                lines.append(f"{prefix}{key}: {_format_value(nested)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}(none)"]
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(_render_structured(item, indent=indent + 2))
            else:
                lines.append(f"{prefix}- {_format_value(item)}")
        return lines
    return [f"{prefix}{_format_value(value)}"]


def _clip_text(text: str, *, limit: int = _MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


def _sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return _clip_text(value.replace("\r", " ").replace("\n", "\\n"))
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(v) for v in value]
    return _clip_text(repr(value))


def _format_value(value: Any) -> str:
    normalized = _sanitize(value)
    if isinstance(normalized, str):
        return json.dumps(normalized, ensure_ascii=False)
    if isinstance(normalized, bool):
        return "true" if normalized else "false"
    if normalized is None:
        return "null"
    if isinstance(normalized, (int, float)):
        return str(normalized)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def _stable_dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        return repr(value)
