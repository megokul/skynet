"""
Strict developer trace formatter with SSH-only remote persistence.

This module is the single active trace system for orchestration boundaries.
"""

from __future__ import annotations

import contextvars
import dataclasses
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
import textwrap
import time
from typing import Any

import paramiko


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEPARATOR = "=" * 80
_SUMMARY_SEPARATOR = "-" * 80
_MAX_TEXT = 2000
_BOX_WIDTH = 79
_BOX_CONTENT = _BOX_WIDTH - 3        # │ + space + 76 chars + │ = 79
_DECISION_BOX_WIDTH = 66             # ┌ + 64×╌ + ┐
_DECISION_INNER = 63                 # ╎ + space + 63 chars + ╎ = 66
_THICK_SEPARATOR = "━" * _BOX_WIDTH
_WINDOWS_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
_REMOTE_TRACE_PATH = r"E:\SKYNET-SANDBOX\logs\skynet.trace.log"

_WRITE_LOCK = threading.Lock()
_TRACE_CONTEXT: contextvars.ContextVar["DevTraceSession | None"] = contextvars.ContextVar(
    "skynet_dev_trace_session",
    default=None,
)


class DevTracePhase(IntEnum):
    ENTRY = 1
    INTENT = 2
    ROUTING = 3
    SPECIALIST = 4
    RESTORATION = 5
    RESPONSE = 6


@dataclass(slots=True)
class _PromptInfo:
    prompt_file: str
    model: str


@dataclass(slots=True)
class _StateMutation:
    key: str
    old_value: Any
    new_value: Any


@dataclass(slots=True)
class DevTraceNode:
    node_id: int
    phase: DevTracePhase
    file: str
    line: int
    function: str
    params: dict[str, Any] = field(default_factory=dict)
    parent_id: int | None = None
    children: list[int] = field(default_factory=list)
    order: int = 0
    context: dict[str, Any] = field(default_factory=dict)
    prompts: list[_PromptInfo] = field(default_factory=list)
    state_changes: list[_StateMutation] = field(default_factory=list)
    returns: dict[str, Any] = field(default_factory=dict)
    data_flow: list[tuple[str, Any, str, Any]] = field(default_factory=list)
    decision: dict[str, Any] = field(default_factory=dict)


class DevTraceSession:
    """One in-memory trace session for a single conversation turn."""

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
        self.user_input = str(user_input or "")
        self.timestamp = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        self._lock = threading.RLock()
        self._start_perf = time.perf_counter()
        self._ended = False
        self._next_node_id = 1
        self._next_order = 1

        self._nodes: dict[int, DevTraceNode] = {}
        self._roots: list[int] = []
        self._phase_last_node: dict[DevTracePhase, int] = {}
        self._frame_latest: dict[tuple[str, str], int] = {}
        self._role_chain: list[str] = []
        self._decision_points = 0
        self._state_mutation_counts: dict[str, int] = {}

        self._header: dict[str, Any] = {
            "conversation_id": None,
            "telegram_user_id": self.user_id if self.user_id else None,
            "active_role": None,
            "message_count_before": None,
            "message_count_after": None,
            "turn": None,
            "final_active_role": None,
        }
        self._special_nodes: dict[str, int] = {
            "source": 0,
            "run": 0,
            "assistant_message": 0,
            "user_message": 0,
        }

    def set_header_field(self, key: str, value: Any) -> None:
        with self._lock:
            self._header[str(key)] = _sanitize(value)

    def mark_source_node(self, node_id: int | None) -> None:
        if not node_id:
            return
        with self._lock:
            self._special_nodes["source"] = int(node_id)

    def mark_run_node(self, node_id: int | None) -> None:
        if not node_id:
            return
        with self._lock:
            self._special_nodes["run"] = int(node_id)

    def mark_assistant_message_node(self, node_id: int | None) -> None:
        if not node_id:
            return
        with self._lock:
            self._special_nodes["assistant_message"] = int(node_id)

    def mark_user_message_node(self, node_id: int | None) -> None:
        if not node_id:
            return
        with self._lock:
            self._special_nodes["user_message"] = int(node_id)

    def record_control_flow(
        self,
        phase: DevTracePhase | int,
        *,
        file: str | None = None,
        line: int | None = None,
        function: str | None = None,
        parent_id: int | None = None,
        params: dict[str, Any] | None = None,
        stack_depth: int = 2,
    ) -> int:
        with self._lock:
            resolved_phase = DevTracePhase(int(phase))
            captured_file, captured_line, captured_function, captured_params = _capture_callsite_and_params(
                stack_depth=stack_depth
            )

            final_file = str(file or captured_file)
            final_line = int(line or captured_line or 0)
            final_function = str(function or captured_function)

            if params is None:
                param_payload = _sanitize(captured_params)
            else:
                param_payload = _sanitize(params)
            if not isinstance(param_payload, dict):
                param_payload = {"value": param_payload}

            inferred_parent = parent_id if parent_id in self._nodes else None
            if inferred_parent is None:
                inferred_parent = self._infer_parent_from_stack(stack_depth=stack_depth)

            node_id = self._next_node_id
            self._next_node_id += 1

            node = DevTraceNode(
                node_id=node_id,
                phase=resolved_phase,
                file=final_file,
                line=final_line,
                function=final_function,
                params=param_payload,
                parent_id=inferred_parent,
                order=self._next_order,
            )
            self._next_order += 1
            self._nodes[node_id] = node
            self._phase_last_node[resolved_phase] = node_id
            self._frame_latest[(final_file, final_function)] = node_id

            if inferred_parent is not None and inferred_parent in self._nodes:
                self._nodes[inferred_parent].children.append(node_id)
            else:
                self._roots.append(node_id)
            return node_id

    def _infer_parent_from_stack(self, *, stack_depth: int) -> int | None:
        frame = inspect.currentframe()
        try:
            walker = frame
            for _ in range(stack_depth + 1):
                if walker is None:
                    return None
                walker = walker.f_back
            while walker is not None:
                key = (_to_repo_relative(walker.f_code.co_filename), str(walker.f_code.co_name))
                node_id = self._frame_latest.get(key)
                if node_id is not None and node_id in self._nodes:
                    return node_id
                walker = walker.f_back
            return None
        finally:
            del frame

    def _resolve_node(self, phase: DevTracePhase | int, node_id: int | None = None) -> DevTraceNode | None:
        if node_id is not None and node_id in self._nodes:
            return self._nodes[node_id]
        resolved = DevTracePhase(int(phase))
        last_id = self._phase_last_node.get(resolved)
        if last_id is None:
            return None
        return self._nodes.get(last_id)

    def record_context(
        self,
        phase: DevTracePhase | int,
        *,
        values: dict[str, Any],
        node_id: int | None = None,
    ) -> None:
        with self._lock:
            node = self._resolve_node(phase, node_id=node_id)
            if node is None:
                return
            for key, value in values.items():
                node.context[str(key)] = _sanitize(value)

    def record_prompt(
        self,
        phase: DevTracePhase | int,
        *,
        prompt_file: str,
        model: str,
        node_id: int | None = None,
    ) -> None:
        with self._lock:
            node = self._resolve_node(phase, node_id=node_id)
            if node is None:
                return
            node.prompts.append(_PromptInfo(prompt_file=str(prompt_file), model=str(model)))

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
        source_clean = _sanitize(source_value)
        target_clean = _sanitize(target_value)
        if _stable_dump(source_clean) == _stable_dump(target_clean):
            return
        with self._lock:
            node = self._resolve_node(phase, node_id=node_id)
            if node is None:
                return
            node.data_flow.append((str(source_name), source_clean, str(target_name), target_clean))

    def record_decision(
        self,
        phase: DevTracePhase | int,
        reasoning: Any,
        *,
        node_id: int | None = None,
    ) -> None:
        with self._lock:
            self._decision_points += 1
            node = self._resolve_node(phase, node_id=node_id)
            if node is None:
                return
            if isinstance(reasoning, dict):
                node.decision = {str(k): _sanitize(v) for k, v in reasoning.items()}
            elif reasoning is not None:
                node.decision = {"reasoning": _sanitize(reasoning)}

    def record_state_mutation(
        self,
        phase: DevTracePhase | int,
        *,
        key: str,
        old_value: Any,
        new_value: Any,
        node_id: int | None = None,
    ) -> None:
        old_clean = _sanitize(old_value)
        new_clean = _sanitize(new_value)
        if _stable_dump(old_clean) == _stable_dump(new_clean):
            return
        with self._lock:
            node = self._resolve_node(phase, node_id=node_id)
            if node is None:
                return
            entry = _StateMutation(
                key=str(key),
                old_value=old_clean,
                new_value=new_clean,
            )
            node.state_changes.append(entry)
            self._state_mutation_counts[entry.key] = self._state_mutation_counts.get(entry.key, 0) + 1

    def record_output(
        self,
        phase: DevTracePhase | int,
        *,
        key: str,
        value: Any,
        node_id: int | None = None,
    ) -> None:
        with self._lock:
            node = self._resolve_node(phase, node_id=node_id)
            if node is None:
                return
            node.returns[str(key)] = _sanitize(value)

    def record_role_enter(
        self,
        phase: DevTracePhase | int,
        role: str,
        *,
        node_id: int | None = None,
    ) -> None:
        role_name = (role or "").strip()
        if not role_name:
            return
        with self._lock:
            del phase, node_id
            if not self._role_chain or self._role_chain[-1] != role_name:
                self._role_chain.append(role_name)

    def record_role_switch(
        self,
        phase: DevTracePhase | int,
        *,
        from_role: str,
        to_role: str,
        node_id: int | None = None,
    ) -> None:
        from_name = (from_role or "").strip() or "unknown"
        to_name = (to_role or "").strip() or "unknown"
        with self._lock:
            del phase, node_id
            if not self._role_chain:
                self._role_chain.append(from_name)
            elif self._role_chain[-1] != from_name:
                self._role_chain.append(from_name)
            if self._role_chain[-1] != to_name:
                self._role_chain.append(to_name)

    def end(self) -> None:
        with self._lock:
            if self._ended:
                return
            self._ended = True
            elapsed_ms = int(round((time.perf_counter() - self._start_perf) * 1000.0))
            text = self.render(total_execution_ms=elapsed_ms)
        _append_trace_block(text)

    def render(self, *, total_execution_ms: int) -> str:
        with self._lock:
            lines: list[str] = []

            # ── header box ──────────────────────────────────────────────────
            lines.extend(self._render_header_box(total_execution_ms))
            lines.append("")

            # ── execution tree ───────────────────────────────────────────────
            lines.append("▼ EXECUTION")
            lines.append("│")

            source_id = self._special_nodes.get("source") or 0
            run_id = self._special_nodes.get("run") or 0
            assistant_id = self._special_nodes.get("assistant_message") or 0
            user_node_id = self._special_nodes.get("user_message") or 0
            hidden: set[int] = {nid for nid in (user_node_id,) if nid}

            ordered: list[int] = []
            if source_id and source_id in self._nodes:
                ordered.append(source_id)
                hidden.add(source_id)
            if run_id and run_id in self._nodes:
                ordered.append(run_id)
                hidden.add(run_id)
            for root_id in sorted(self._roots, key=lambda r: self._nodes[r].order):
                if root_id not in hidden and root_id != assistant_id:
                    ordered.append(root_id)
                    hidden.add(root_id)
            if assistant_id and assistant_id in self._nodes:
                ordered.append(assistant_id)

            for idx, nid in enumerate(ordered):
                is_last = idx == len(ordered) - 1
                lines.extend(self._render_node(nid, prefix="", is_last=is_last, depth=0))
                if not is_last:
                    lines.append("│")

            lines.append("")

            # ── state changes ────────────────────────────────────────────────
            mutations = self._collect_all_state_mutations()
            role_chain_str = " → ".join(self._role_chain) if len(self._role_chain) >= 2 else ""
            if mutations or role_chain_str:
                lines.append("▼ STATE CHANGES")
                for mut in mutations:
                    lines.append(
                        f"│  ✎ {mut.key}: {_format_inline(mut.old_value)} → {_format_inline(mut.new_value)}"
                    )
                if role_chain_str:
                    lines.append(f"│  ⟳ role: {role_chain_str}")
                lines.append("")

            lines.append(_THICK_SEPARATOR)
            lines.append("END TRACE")
            lines.append(_THICK_SEPARATOR)
            return "\n".join(lines) + "\n"

    def _render_header_box(self, total_ms: int) -> list[str]:
        lines: list[str] = []
        horiz = "─" * (_BOX_WIDTH - 2)
        lines.append(f"┌{horiz}┐")

        turn = self._header.get("turn")
        trace_text = self.trace_id
        if turn is not None:
            trace_text += f" | turn={_format_plain(turn)}"
        lines.append(_box_line("TRACE", trace_text))
        lines.append(_box_line("TIME", f"{self.timestamp}  ({total_ms}ms)"))

        uid = self._header.get("telegram_user_id")
        if uid is not None and str(uid).strip():
            lines.append(_box_line("USER", str(uid)))

        active_role = self._header.get("active_role")
        if active_role is not None and str(active_role).strip():
            if len(self._role_chain) >= 2:
                role_display = " → ".join(self._role_chain)
            else:
                role_display = str(active_role)
            lines.append(_box_line("ROLE", role_display))

        msg_before = self._header.get("message_count_before")
        msg_after = self._header.get("message_count_after")
        if msg_before is not None and msg_after is not None:
            lines.append(_box_line("MSGS", f"{_format_plain(msg_before)} → {_format_plain(msg_after)}"))
        elif msg_before is not None:
            lines.append(_box_line("MSGS", str(_format_plain(msg_before))))

        lines.append(f"├{horiz}┤")
        lines.append(_box_line("INPUT", _format_inline(self.user_input)))

        run_id = self._special_nodes.get("run") or 0
        output: Any = None
        if run_id and run_id in self._nodes:
            n = self._nodes[run_id]
            output = (
                n.returns.get("response")
                or n.returns.get("response_text")
                or n.returns.get("assistant_response")
            )
        if output is not None:
            lines.append(_box_line("OUTPUT", _format_inline(output)))

        lines.append(f"└{horiz}┘")
        return lines

    def _collect_all_state_mutations(self) -> list[_StateMutation]:
        result: list[_StateMutation] = []
        for node_id in sorted(self._nodes, key=lambda n: self._nodes[n].order):
            result.extend(self._nodes[node_id].state_changes)
        return result

    def _render_node(self, node_id: int, *, prefix: str, is_last: bool, depth: int) -> list[str]:
        node = self._nodes[node_id]
        lines: list[str] = []

        # call line: ├─► / └─►  (one extra dash per depth level)
        dashes = "─" * (depth + 1)
        connector = "└" if is_last else "├"
        params_str = _format_params_inline(node.params)
        lines.append(f"{prefix}{connector}{dashes}► {node.file}:{node.line} → {node.function}({params_str})")

        # prefix for body lines below this call
        child_prefix = prefix + ("    " if is_last else "│   ")

        # context entries (inline, no header label)
        if node.context:
            for key, value in node.context.items():
                lines.append(f"{child_prefix}│  {key}={_format_value(value)}")

        # decision block
        if node.decision:
            lines.append(f"{child_prefix}│")
            lines.extend(
                _render_decision_box(
                    node.decision, node.prompts, node.returns,
                    box_prefix=child_prefix + "│  ",
                )
            )

        # children
        children = [cid for cid in node.children if cid in self._nodes]
        if children:
            lines.append(f"{child_prefix}│")
            for i, child_id in enumerate(children):
                child_is_last = i == len(children) - 1
                lines.extend(
                    self._render_node(child_id, prefix=child_prefix, is_last=child_is_last, depth=depth + 1)
                )
                if not child_is_last:
                    lines.append(f"{child_prefix}│")

        # return values
        ret_items = _iter_return_items_v3(node.returns)
        if ret_items:
            parts = [f"{k}={_format_inline(v)}" for k, v in ret_items]
            lines.append(f"{child_prefix}← {', '.join(parts)}")

        return lines


def _iter_return_items(values: dict[str, Any]) -> list[tuple[str, Any]]:
    """Legacy: kept for compatibility with callers that filter out response keys."""
    rendered: list[tuple[str, Any]] = []
    for key, value in values.items():
        if key in {"assistant_response", "response_text", "response"}:
            continue
        if isinstance(value, dict) and key.endswith("_result"):
            for nested_key, nested_value in value.items():
                rendered.append((str(nested_key), nested_value))
            continue
        rendered.append((str(key), value))
    return rendered


def _iter_return_items_v3(values: dict[str, Any]) -> list[tuple[str, Any]]:
    """v3: includes response keys so they appear on the ← return line."""
    rendered: list[tuple[str, Any]] = []
    for key, value in values.items():
        if isinstance(value, dict) and key.endswith("_result"):
            for nested_key, nested_value in value.items():
                rendered.append((str(nested_key), nested_value))
            continue
        rendered.append((str(key), value))
    return rendered


def _format_params_inline(params: dict[str, Any]) -> str:
    """Format function parameters as a compact inline string."""
    if not params:
        return ""
    parts = [f"{k}={_format_inline(v)}" for k, v in params.items()]
    result = ", ".join(parts)
    return result[:120] + ("…" if len(result) > 120 else "")


def _box_line(label: str, value: str) -> str:
    """Single row in the 79-wide header box: │ LABEL  value...padding│."""
    content = f"{label:<6}{value}"
    if len(content) > _BOX_CONTENT:
        content = content[:_BOX_CONTENT - 1] + "…"
    return f"│ {content:<{_BOX_CONTENT}}│"


def _render_decision_box(
    decision: dict[str, Any],
    prompts: list[_PromptInfo],
    returns: dict[str, Any],
    *,
    box_prefix: str,
) -> list[str]:
    """Render a dashed-border decision block (╌╌╌ style)."""
    lines: list[str] = []
    dashes = "╌" * (_DECISION_BOX_WIDTH - 2)
    inner = _DECISION_INNER

    def row(text: str = "") -> str:
        truncated = text[:inner]
        return f"{box_prefix}╎ {truncated:<{inner}}╎"

    lines.append(f"{box_prefix}┌{dashes}┐")

    # decision name
    routing_rule = str(decision.get("routing_rule") or "")
    selected_intent = str(decision.get("selected_intent") or "")
    name = routing_rule or selected_intent or "decision"
    lines.append(row(f"🧠 DECISION: {name}"))
    lines.append(row())

    # LLM call block vs rule match block
    if prompts:
        lines.append(row("🤖 LLM CALL"))
        seen_models: set[str] = set()
        for p in prompts:
            model_str = p.model if p.model else "router:auto"
            if model_str not in seen_models:
                lines.append(row(f"   model:   {model_str}"))
                seen_models.add(model_str)
        for p in prompts:
            lines.append(row(f"   prompt:  {p.prompt_file}"))
    else:
        lines.append(row("📋 RULE MATCH"))
        reasoning_str = str(decision.get("reasoning") or "")
        if reasoning_str:
            wrapped = textwrap.wrap(reasoning_str, width=inner - 14, break_long_words=False)
            if wrapped:
                lines.append(row(f"   rule:    {wrapped[0]}"))
                for extra in wrapped[1:]:
                    lines.append(row(f"            {extra}"))
        for k, v in decision.items():
            if k in ("reasoning", "routing_rule", "selected_intent"):
                continue
            lines.append(row(f"   {k:<12}{_format_plain(v)}"))

    lines.append(row())

    # result line
    confidence = decision.get("classifier_confidence")
    if selected_intent:
        conf_str = f" (confidence={confidence})" if confidence is not None else ""
        if confidence is not None and float(confidence) < 0.70:
            lines.append(row(f"⚠  LOW CONF ({confidence} < 0.70) → fallback applied"))
        else:
            lines.append(row(f"✅ RESULT: intent={_format_inline(selected_intent)}{conf_str}"))
    else:
        ret_items = [
            f"{k}={_format_inline(v)}"
            for k, v in returns.items()
            if k not in ("response", "assistant_response", "response_text")
        ]
        if ret_items:
            lines.append(row(f"✅ RESULT: {', '.join(ret_items)}"))
        else:
            reasoning_str = str(decision.get("reasoning") or "")
            if reasoning_str:
                lines.append(row(f"✅ RESULT: {reasoning_str}"))

    lines.append(f"{box_prefix}└{dashes}┘")
    return lines


def _render_param_lines(key: str, value: Any, *, indent: str) -> list[str]:
    normalized = _sanitize(value)
    if isinstance(normalized, (list, dict)):
        pretty = json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2)
        pretty_lines = pretty.splitlines()
        if not pretty_lines:
            return [f"{indent}{key}={_format_value(normalized)}"]
        rendered = [f"{indent}{key}={pretty_lines[0]}"]
        for line in pretty_lines[1:]:
            rendered.append(f"{indent}{line}")
        return rendered

    value_text = _format_value(normalized)
    if isinstance(normalized, str) and len(value_text) > 140:
        wrapped = textwrap.wrap(
            value_text,
            width=120,
            break_long_words=False,
            break_on_hyphens=False,
        )
        if not wrapped:
            return [f"{indent}{key}={value_text}"]
        rendered = [f"{indent}{key}={wrapped[0]}"]
        for line in wrapped[1:]:
            rendered.append(f"{indent}{line}")
        return rendered
    return [f"{indent}{key}={value_text}"]


def start_trace_session(
    *,
    trace_id: str,
    user_id: str | int,
    user_input: str,
) -> tuple[DevTraceSession, contextvars.Token]:
    session = DevTraceSession(trace_id=trace_id, user_id=user_id, user_input=user_input)
    token = _TRACE_CONTEXT.set(session)
    return session, token


def get_current_trace_session() -> DevTraceSession | None:
    return _TRACE_CONTEXT.get()


def clear_trace_session(token: contextvars.Token | None = None) -> None:
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
    stack_depth: int = 2,
) -> int | None:
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
        stack_depth=stack_depth,
    )


def trace_context(
    phase: DevTracePhase | int,
    *,
    values: dict[str, Any],
    node_id: int | None = None,
) -> None:
    session = get_current_trace_session()
    if session is None:
        return
    session.record_context(phase, values=values, node_id=node_id)


def trace_prompt(
    phase: DevTracePhase | int,
    *,
    prompt_file: str,
    model: str,
    node_id: int | None = None,
) -> None:
    session = get_current_trace_session()
    if session is None:
        return
    session.record_prompt(
        phase,
        prompt_file=prompt_file,
        model=model,
        node_id=node_id,
    )


def trace_data_flow(
    phase: DevTracePhase | int,
    *,
    source_name: str,
    source_value: Any,
    target_name: str,
    target_value: Any,
    node_id: int | None = None,
) -> None:
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
    session = get_current_trace_session()
    if session is None:
        return
    session.record_role_switch(
        phase,
        from_role=from_role,
        to_role=to_role,
        node_id=node_id,
    )


def _append_trace_block(text: str) -> None:
    payload = (text or "").replace("\r\n", "\n")
    if not payload.strip():
        return
    with _WRITE_LOCK:
        _append_trace_over_ssh(payload)


def _append_trace_over_ssh(payload: str) -> None:
    host = _required_env_any("TRACE_SSH_HOST", "OPENCLAW_SSH_HOST")
    user = _required_env_any("TRACE_SSH_USER", "OPENCLAW_SSH_USER")
    key_path = _required_env_any("TRACE_SSH_KEY_PATH", "OPENCLAW_SSH_KEY_PATH")
    port = _int_env_any(default=22, names=("TRACE_SSH_PORT", "OPENCLAW_SSH_PORT"))
    remote_path = _resolve_remote_trace_path()

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
            port=port,
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


def _required_env_any(*names: str) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    joined = ", ".join(f"`{name}`" for name in names)
    message = f"TRACE SSH ERROR: required environment variable is not set; expected one of: {joined}"
    print(message)
    raise RuntimeError(message)


def _int_env_any(*, default: int, names: tuple[str, ...]) -> int:
    for name in names:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            continue
        try:
            return int(raw)
        except ValueError:
            continue
    return int(default)


def _resolve_remote_trace_path() -> str:
    configured = (os.getenv("TRACE_REMOTE_PATH") or "").strip()
    if not configured:
        return _REMOTE_TRACE_PATH
    if _normalize_windows_path(configured) != _normalize_windows_path(_REMOTE_TRACE_PATH):
        message = (
            "TRACE SSH ERROR: `TRACE_REMOTE_PATH` must be "
            f"`{_REMOTE_TRACE_PATH}` for worker-only trace persistence"
        )
        print(message)
        raise RuntimeError(message)
    return configured


def _normalize_windows_path(path: str) -> str:
    return str(path or "").strip().replace("/", "\\").rstrip("\\").lower()


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
    candidates = [remote_path]
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
                return ("unknown", 0, "unknown", {})
            walker = walker.f_back
        if walker is None:
            return ("unknown", 0, "unknown", {})

        arg_info = inspect.getargvalues(walker)
        params: dict[str, Any] = {}
        for arg_name in arg_info.args:
            if arg_name == "self":
                value = walker.f_locals.get(arg_name)
                if value is None:
                    params[arg_name] = "<self>"
                else:
                    params[arg_name] = f"<{value.__class__.__name__}>"
            else:
                params[arg_name] = _sanitize(walker.f_locals.get(arg_name))

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


def _sanitize(value: Any) -> Any:
    if isinstance(value, str):
        clean = value.replace("\r", " ").replace("\n", "\\n")
        return clean[:_MAX_TEXT] + ("...<truncated>" if len(clean) > _MAX_TEXT else "")
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if dataclasses.is_dataclass(value):
        sanitized: dict[str, Any] = {}
        for item in dataclasses.fields(value):
            try:
                sanitized[str(item.name)] = _sanitize(getattr(value, item.name))
            except Exception:
                sanitized[str(item.name)] = "<unavailable>"
        return sanitized
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(v) for v in value]
    return _sanitize(repr(value))


def _stable_dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        return repr(value)


def _format_inline(value: Any) -> str:
    sanitized = _sanitize(value)
    if isinstance(sanitized, bool):
        return "true" if sanitized else "false"
    if sanitized is None:
        return "null"
    if isinstance(sanitized, (int, float)):
        return str(sanitized)
    if isinstance(sanitized, str):
        return f"\"{sanitized}\""
    return json.dumps(sanitized, ensure_ascii=False, sort_keys=True)


def _format_plain(value: Any) -> str:
    sanitized = _sanitize(value)
    if isinstance(sanitized, bool):
        return "true" if sanitized else "false"
    if sanitized is None:
        return "null"
    if isinstance(sanitized, (int, float)):
        return str(sanitized)
    if isinstance(sanitized, str):
        return sanitized
    return json.dumps(sanitized, ensure_ascii=False, sort_keys=True)


def _format_value(value: Any) -> str:
    return _format_inline(value)
