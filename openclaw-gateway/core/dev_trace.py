"""
Structured YAML-style execution ledger for orchestration boundary tracing.

This is the single active trace system. It buffers one conversation-turn trace
in memory and appends it to a remote file over SSH at session end.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
import inspect
import json
import os
from pathlib import Path
import posixpath
import re
import shlex
import threading
import time
from typing import Any

import paramiko


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEPARATOR = "=" * 80
_MAX_TEXT = 2000
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_WINDOWS_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")

_WRITE_LOCK = threading.Lock()
_TRACE_CONTEXT: contextvars.ContextVar["DevTraceSession | None"] = contextvars.ContextVar(
    "skynet_dev_trace_session",
    default=None,
)


class DevTracePhase(IntEnum):
    """Fixed cognitive phases retained for orchestration mapping."""

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

    @property
    def title(self) -> str:
        return f"PHASE {self.value} - {self.label}"


@dataclass(slots=True)
class DevTraceNode:
    """One call-sequence entry in the ledger."""

    node_id: int
    phase: DevTracePhase
    depth: int
    file: str
    line: int
    function: str
    params: dict[str, Any] = field(default_factory=dict)
    parent_id: int | None = None
    children: list["DevTraceNode"] = field(default_factory=list)
    prompt: dict[str, str] | None = None
    data_flow: list[dict[str, Any]] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    state_change: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions: list[Any] = field(default_factory=list)
    role_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class _PhaseBuffer:
    phase: DevTracePhase
    roots: list[DevTraceNode] = field(default_factory=list)
    node_index: dict[int, DevTraceNode] = field(default_factory=dict)
    last_node_id: int | None = None


class DevTraceSession:
    """In-memory trace session for one conversation turn."""

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
        self._data_lineage: dict[str, Any] = {}
        self._call_sequence: list[DevTraceNode] = []

        self._phases: dict[DevTracePhase, _PhaseBuffer] = {
            phase: _PhaseBuffer(phase=phase)
            for phase in DevTracePhase
        }

    def _phase(self, phase: DevTracePhase | int) -> _PhaseBuffer:
        return self._phases[DevTracePhase(int(phase))]

    def _record_control_flow_locked(
        self,
        phase: DevTracePhase | int,
        *,
        file: str | None,
        line: int | None,
        function: str | None,
        parent_id: int | None,
        params: dict[str, Any] | None,
        stack_depth: int,
    ) -> int:
        resolved_phase = DevTracePhase(int(phase))
        captured_file = "unknown"
        captured_line = 0
        captured_function = "unknown"
        captured_params: dict[str, Any] = {}
        if not file or not line or not function or params is None:
            captured_file, captured_line, captured_function, captured_params = _capture_callsite_and_params(
                stack_depth=stack_depth + 1
            )

        file_text = str(file or captured_file)
        line_value = int(line or captured_line or 0)
        function_text = str(function or captured_function)
        params_payload = _sanitize(params if params is not None else captured_params)
        if not isinstance(params_payload, dict):
            params_payload = {"value": params_payload}

        phase_buffer = self._phase(resolved_phase)
        parent = phase_buffer.node_index.get(parent_id) if parent_id else None
        depth = parent.depth + 1 if parent is not None else 0

        node_id = self._next_node_id
        self._next_node_id += 1
        node = DevTraceNode(
            node_id=node_id,
            phase=resolved_phase,
            depth=depth,
            file=file_text,
            line=line_value,
            function=function_text,
            params=params_payload,
            parent_id=parent_id,
        )

        if parent is not None:
            parent.children.append(node)
        else:
            phase_buffer.roots.append(node)
        phase_buffer.node_index[node_id] = node
        phase_buffer.last_node_id = node_id
        self._call_sequence.append(node)
        return node_id

    def record_control_flow(
        self,
        phase: DevTracePhase | int,
        *,
        file: str | None = None,
        line: int | None = None,
        function: str | None = None,
        parent_id: int | None = None,
        params: dict[str, Any] | None = None,
        stack_depth: int = 1,
    ) -> int:
        """Record a call-sequence node (call-site preferred)."""
        with self._lock:
            return self._record_control_flow_locked(
                phase,
                file=file,
                line=line,
                function=function,
                parent_id=parent_id,
                params=params,
                stack_depth=stack_depth + 1,
            )

    def _resolve_node_locked(
        self,
        phase: DevTracePhase | int,
        *,
        node_id: int | None = None,
    ) -> DevTraceNode:
        resolved_phase = DevTracePhase(int(phase))
        phase_buffer = self._phase(resolved_phase)
        if node_id is not None:
            node = phase_buffer.node_index.get(node_id)
            if node is not None:
                return node
        if phase_buffer.last_node_id is not None:
            existing = phase_buffer.node_index.get(phase_buffer.last_node_id)
            if existing is not None:
                return existing
        created = self._record_control_flow_locked(
            resolved_phase,
            file="unknown",
            line=0,
            function="unknown",
            parent_id=None,
            params={},
            stack_depth=2,
        )
        return phase_buffer.node_index[created]

    def record_prompt(
        self,
        phase: DevTracePhase | int,
        *,
        prompt_file: str,
        model: str,
        node_id: int | None = None,
    ) -> None:
        """Attach prompt metadata to a call-sequence entry."""
        with self._lock:
            node = self._resolve_node_locked(phase, node_id=node_id)
            node.prompt = {
                "file": str(prompt_file or ""),
                "model": str(model or ""),
            }

    def record_data_flow(
        self,
        phase: DevTracePhase | int,
        *,
        source_name: str,
        source_value: Any,
        target_name: str,
        target_value: Any,
        node_id: int | None = None,
    ) -> None:
        """Record data transformation and update lineage map."""
        source_clean = _sanitize(source_value)
        target_clean = _sanitize(target_value)
        if _stable_dump(source_clean) == _stable_dump(target_clean):
            return
        with self._lock:
            node = self._resolve_node_locked(phase, node_id=node_id)
            transform = {
                "from": str(source_name),
                "to": str(target_name),
                "value": target_clean,
            }
            node.data_flow.append(transform)
            self._data_lineage[str(target_name)] = target_clean

    def record_decision(
        self,
        phase: DevTracePhase | int,
        reasoning: Any,
        *,
        node_id: int | None = None,
    ) -> None:
        """Record one structured reasoning block."""
        with self._lock:
            node = self._resolve_node_locked(phase, node_id=node_id)
            node.decisions.append(_sanitize(reasoning))
            self._decision_points += 1

    def record_state_mutation(
        self,
        phase: DevTracePhase | int,
        *,
        key: str,
        old_value: Any,
        new_value: Any,
        node_id: int | None = None,
    ) -> None:
        """Record state changes only when a value actually mutates."""
        old_clean = _sanitize(old_value)
        new_clean = _sanitize(new_value)
        if _stable_dump(old_clean) == _stable_dump(new_clean):
            return
        mutation_key = str(key)
        with self._lock:
            node = self._resolve_node_locked(phase, node_id=node_id)
            node.state_change[mutation_key] = {
                "before": old_clean,
                "after": new_clean,
            }
            self._state_mutation_counts[mutation_key] = self._state_mutation_counts.get(mutation_key, 0) + 1

    def record_output(
        self,
        phase: DevTracePhase | int,
        *,
        key: str,
        value: Any,
        node_id: int | None = None,
    ) -> None:
        """Record output field on the nearest call-sequence entry."""
        with self._lock:
            node = self._resolve_node_locked(phase, node_id=node_id)
            node.output[str(key)] = _sanitize(value)

    def record_role_enter(
        self,
        phase: DevTracePhase | int,
        role: str,
        *,
        node_id: int | None = None,
    ) -> None:
        """Record role enter event and update role chain."""
        resolved = (role or "").strip()
        if not resolved:
            return
        with self._lock:
            node = self._resolve_node_locked(phase, node_id=node_id)
            node.role_events.append(
                {
                    "type": "enter",
                    "role": resolved,
                }
            )
            if not self._role_chain or self._role_chain[-1] != resolved:
                self._role_chain.append(resolved)

    def record_role_switch(
        self,
        phase: DevTracePhase | int,
        *,
        from_role: str,
        to_role: str,
        node_id: int | None = None,
    ) -> None:
        """Record role switch event and update role chain."""
        from_name = (from_role or "").strip() or "unknown"
        to_name = (to_role or "").strip() or "unknown"
        with self._lock:
            node = self._resolve_node_locked(phase, node_id=node_id)
            node.role_events.append(
                {
                    "type": "switch",
                    "from": from_name,
                    "to": to_name,
                }
            )
            if not self._role_chain:
                self._role_chain.append(from_name)
            elif self._role_chain[-1] != from_name:
                self._role_chain.append(from_name)
            if self._role_chain[-1] != to_name:
                self._role_chain.append(to_name)

    def end(self) -> None:
        """Render and append this trace block once."""
        with self._lock:
            if self._ended:
                return
            self._ended = True
            elapsed_ms = int(round((time.perf_counter() - self._start_perf) * 1000.0))
            text = self.render(total_execution_ms=elapsed_ms)
        _append_trace_block(text)

    def render(self, *, total_execution_ms: int) -> str:
        """Render trace as structured YAML-style ledger text."""
        lines: list[str] = [
            _SEPARATOR,
            f"trace_id: {self.trace_id}",
            f"timestamp: {self.timestamp}",
            f"user_id: {self.user_id}",
            "input:",
            f"  text: {_yaml_value(self.user_input)}",
            _SEPARATOR,
            "",
            "call_sequence:",
        ]

        if self._call_sequence:
            for index, node in enumerate(self._call_sequence):
                if index > 0:
                    lines.append("")
                lines.extend(_render_call_node(node))
        else:
            lines.append("  []")

        lines.append("")
        lines.append("data_lineage:")
        if self._data_lineage:
            for key, value in self._data_lineage.items():
                lines.append(f"  {_yaml_key(key)}: {_yaml_value(value)}")
        else:
            lines.append("  {}")

        lines.append("")
        lines.append("summary:")
        lines.append(f"  total_time_ms: {int(total_execution_ms)}")
        lines.append(f"  decisions: {self._decision_points}")
        lines.append(f"  role_chain: {_yaml_value(self._role_chain)}")
        lines.append(f"  state_mutations: {_yaml_value(self._state_mutation_counts)}")
        lines.append("")
        lines.append(_SEPARATOR)
        lines.append("END TRACE")
        lines.append(_SEPARATOR)
        return "\n".join(lines) + "\n"


def start_trace_session(
    *,
    trace_id: str,
    user_id: str | int,
    user_input: str,
) -> tuple[DevTraceSession, contextvars.Token]:
    """Create and bind a trace session in task-local context."""
    session = DevTraceSession(trace_id=trace_id, user_id=user_id, user_input=user_input)
    token = _TRACE_CONTEXT.set(session)
    return session, token


def get_current_trace_session() -> DevTraceSession | None:
    """Return active task-local trace session."""
    return _TRACE_CONTEXT.get()


def clear_trace_session(token: contextvars.Token | None = None) -> None:
    """Clear task-local trace session."""
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
    params: dict[str, Any] | None = None,
    stack_depth: int = 1,
) -> int | None:
    """Wrapper for `DevTraceSession.record_control_flow`."""
    session = get_current_trace_session()
    if session is None:
        return None
    return session.record_control_flow(
        phase,
        file=file,
        line=line,
        function=function,
        parent_id=parent_id,
        params=params,
        stack_depth=stack_depth + 1,
    )


def trace_prompt(
    phase: DevTracePhase | int,
    *,
    prompt_file: str,
    model: str,
    node_id: int | None = None,
) -> None:
    """Wrapper for `DevTraceSession.record_prompt`."""
    session = get_current_trace_session()
    if session is None:
        return
    session.record_prompt(phase, prompt_file=prompt_file, model=model, node_id=node_id)


def trace_data_flow(
    phase: DevTracePhase | int,
    *,
    source_name: str,
    source_value: Any,
    target_name: str,
    target_value: Any,
    node_id: int | None = None,
) -> None:
    """Wrapper for `DevTraceSession.record_data_flow`."""
    session = get_current_trace_session()
    if session is None:
        return
    session.record_data_flow(
        phase,
        source_name=source_name,
        source_value=source_value,
        target_name=target_name,
        target_value=target_value,
        node_id=node_id,
    )


def trace_decision(
    phase: DevTracePhase | int,
    reasoning: Any,
    *,
    node_id: int | None = None,
) -> None:
    """Wrapper for `DevTraceSession.record_decision`."""
    session = get_current_trace_session()
    if session is None:
        return
    session.record_decision(phase, reasoning, node_id=node_id)


def trace_state_mutation(
    phase: DevTracePhase | int,
    *,
    key: str,
    old_value: Any,
    new_value: Any,
    node_id: int | None = None,
) -> None:
    """Wrapper for `DevTraceSession.record_state_mutation`."""
    session = get_current_trace_session()
    if session is None:
        return
    session.record_state_mutation(
        phase,
        key=key,
        old_value=old_value,
        new_value=new_value,
        node_id=node_id,
    )


def trace_output(
    phase: DevTracePhase | int,
    *,
    key: str,
    value: Any,
    node_id: int | None = None,
) -> None:
    """Wrapper for `DevTraceSession.record_output`."""
    session = get_current_trace_session()
    if session is None:
        return
    session.record_output(phase, key=key, value=value, node_id=node_id)


def trace_role_enter(
    phase: DevTracePhase | int,
    role: str,
    *,
    node_id: int | None = None,
) -> None:
    """Wrapper for `DevTraceSession.record_role_enter`."""
    session = get_current_trace_session()
    if session is None:
        return
    session.record_role_enter(phase, role, node_id=node_id)


def trace_role_switch(
    phase: DevTracePhase | int,
    *,
    from_role: str,
    to_role: str,
    node_id: int | None = None,
) -> None:
    """Wrapper for `DevTraceSession.record_role_switch`."""
    session = get_current_trace_session()
    if session is None:
        return
    session.record_role_switch(phase, from_role=from_role, to_role=to_role, node_id=node_id)


def _append_trace_block(text: str) -> None:
    payload = (text or "").replace("\r\n", "\n")
    if not payload.strip():
        return
    with _WRITE_LOCK:
        _append_trace_over_ssh(payload)


def _append_trace_over_ssh(payload: str) -> None:
    host = _required_env("TRACE_SSH_HOST")
    user = _required_env("TRACE_SSH_USER")
    key_path = _required_env("TRACE_SSH_KEY_PATH")
    remote_path = _required_env("TRACE_REMOTE_PATH")

    key_file = Path(key_path).expanduser()
    if not key_file.exists():
        message = f"TRACE SSH ERROR: key file not found at {key_file}"
        print(message)
        raise RuntimeError(message)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            username=user,
            key_filename=str(key_file),
            look_for_keys=False,
            timeout=10,
            auth_timeout=10,
            banner_timeout=10,
        )
        _ensure_remote_directory(client, remote_path)

        sftp = client.open_sftp()
        try:
            last_error: Exception | None = None
            for candidate in _sftp_path_candidates(remote_path):
                try:
                    with sftp.file(candidate, "a") as remote_file:
                        remote_file.write(payload)
                        remote_file.flush()
                    return
                except Exception as exc:
                    last_error = exc
            message = f"TRACE SSH ERROR: failed to open remote append path `{remote_path}`: {last_error}"
            print(message)
            raise RuntimeError(message)
        finally:
            sftp.close()
    except Exception as exc:
        message = f"TRACE SSH ERROR: failed to append remote trace: {exc}"
        print(message)
        raise RuntimeError(message) from exc
    finally:
        client.close()


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if value:
        return value
    message = f"TRACE SSH ERROR: required environment variable `{name}` is not set"
    print(message)
    raise RuntimeError(message)


def _ensure_remote_directory(client: paramiko.SSHClient, remote_path: str) -> None:
    if _WINDOWS_PATH_PATTERN.match(remote_path):
        escaped_path = remote_path.replace("'", "''")
        command = (
            "powershell -NoProfile -Command "
            "\"$path = '{path}'; "
            "$dir = Split-Path -Parent $path; "
            "if ($dir -and -not (Test-Path -LiteralPath $dir)) {{ "
            "New-Item -Path $dir -ItemType Directory -Force | Out-Null "
            "}}\""
        ).format(path=escaped_path)
    else:
        remote_dir = posixpath.dirname(remote_path.replace("\\", "/"))
        if not remote_dir:
            return
        command = f"mkdir -p {shlex.quote(remote_dir)}"

    _, stdout, stderr = client.exec_command(command)
    exit_code = stdout.channel.recv_exit_status()
    if exit_code == 0:
        return
    error_text = (stderr.read().decode("utf-8", errors="replace") or "").strip()
    message = f"TRACE SSH ERROR: failed to create remote directory ({exit_code}): {error_text}"
    print(message)
    raise RuntimeError(message)


def _sftp_path_candidates(remote_path: str) -> list[str]:
    candidates: list[str] = [remote_path]
    normalized = remote_path.replace("\\", "/")
    if normalized not in candidates:
        candidates.append(normalized)
    if _WINDOWS_PATH_PATTERN.match(normalized):
        prefixed = f"/{normalized}"
        if prefixed not in candidates:
            candidates.append(prefixed)
    return candidates


def _capture_callsite_and_params(*, stack_depth: int = 1) -> tuple[str, int, str, dict[str, Any]]:
    frame = inspect.currentframe()
    try:
        walker = frame
        for _ in range(stack_depth + 1):
            if walker is None:
                break
            walker = walker.f_back
        if walker is None:
            return ("unknown", 0, "unknown", {})

        arg_info = inspect.getargvalues(walker)
        params: dict[str, Any] = {}
        for name in arg_info.args:
            if name == "self":
                value = walker.f_locals.get(name)
                params[name] = _clip_text(f"<{value.__class__.__name__}>") if value is not None else "<self>"
            else:
                params[name] = _sanitize(walker.f_locals.get(name))

        if arg_info.varargs:
            params[arg_info.varargs] = _sanitize(walker.f_locals.get(arg_info.varargs))
        if arg_info.keywords:
            params[arg_info.keywords] = _sanitize(walker.f_locals.get(arg_info.keywords))

        return (
            _to_repo_relative(walker.f_code.co_filename),
            int(walker.f_lineno),
            str(walker.f_code.co_name),
            params,
        )
    except Exception:
        return ("unknown", 0, "unknown", {})
    finally:
        del frame


def _to_repo_relative(path: str) -> str:
    try:
        return Path(path).resolve().relative_to(_REPO_ROOT).as_posix()
    except Exception:
        return Path(path).as_posix()


def _render_call_node(node: DevTraceNode) -> list[str]:
    lines = [
        f"  - depth: {node.depth}",
        f"    phase: {_yaml_value(node.phase.title)}",
        f"    file: {_yaml_value(node.file)}",
        f"    line: {node.line}",
        f"    function: {_yaml_value(node.function)}",
        f"    params: {_yaml_value(node.params)}",
    ]

    if node.prompt:
        lines.append("    prompt:")
        lines.append(f"      file: {_yaml_value(node.prompt.get('file', ''))}")
        lines.append(f"      model: {_yaml_value(node.prompt.get('model', ''))}")

    if node.data_flow:
        lines.append("    data_flow:")
        for entry in node.data_flow:
            lines.append(f"      - from: {_yaml_value(entry.get('from', ''))}")
            lines.append(f"        to: {_yaml_value(entry.get('to', ''))}")
            lines.append(f"        value: {_yaml_value(entry.get('value'))}")

    if node.decisions:
        lines.append(f"    decision_reasoning: {_yaml_value(node.decisions)}")

    if node.role_events:
        lines.append(f"    role_events: {_yaml_value(node.role_events)}")

    lines.append(f"    output: {_yaml_value(node.output)}")

    if node.state_change:
        lines.append("    state_change:")
        for key, change in node.state_change.items():
            lines.append(f"      {_yaml_key(key)}:")
            lines.append(f"        before: {_yaml_value(change.get('before'))}")
            lines.append(f"        after: {_yaml_value(change.get('after'))}")
    return lines


def _yaml_key(key: str) -> str:
    text = str(key)
    if _KEY_PATTERN.fullmatch(text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _yaml_value(value: Any) -> str:
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


def _stable_dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        return repr(value)
