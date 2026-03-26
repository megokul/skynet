from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

_PROVIDER_CAPACITY_PATTERNS = (
    "quota exceeded",
    "quota has been reached",
    "daily quota",
    "rate limit",
    "rate-limit",
    "too many requests",
)


@dataclass(frozen=True)
class StageExecutionDeps:
    cfg: Any
    logger: Any
    send_action: Callable[..., Awaitable[dict[str, Any]]]
    send_action_with_heartbeat: Callable[..., Awaitable[dict[str, Any]]]
    get_openclaw_runner: Callable[[], Any]
    stop_request_key: Callable[[int], str]
    stage_payload: Callable[..., dict[str, Any]]
    action_inner_result: Callable[[dict[str, Any]], dict[str, Any]]
    action_exit_code: Callable[[dict[str, Any]], int]
    action_error_text: Callable[[dict[str, Any], str], str]
    action_excerpt: Callable[[dict[str, Any]], str]
    normalize_written_files: Callable[[Any], list[str]]
    has_runnable_written_files: Callable[[list[str]], bool]
    working_dir_has_valid_run_contract: Callable[..., Awaitable[bool]]
    emit_runtime_trace_async: Callable[..., Awaitable[None]]
    build_debug_bundle: Callable[..., dict[str, Any]]
    command_hash: Callable[[str], str]
    runtime_flow: Callable[[], str]
    runtime_transport_label: Callable[..., str]
    runtime_mode_label: Callable[..., str]
    use_acp_orchestration: Callable[[], bool]
    acp_stage_name: Callable[[str], str]
    acp_backend_name: Callable[[str], str]
    record_orchestration_event: Callable[..., Awaitable[None]]
    write_generated_blocks_to_worker: Callable[..., Awaitable[tuple[list[str], str]]]


@dataclass(frozen=True)
class StageFailure:
    stage: str
    returncode: str
    error_excerpt: str
    failure_type: str = ""
    error_code: str = ""

    def as_dict(self) -> dict[str, str]:
        payload = {
            "stage": self.stage,
            "returncode": self.returncode,
            "error_excerpt": self.error_excerpt,
        }
        if self.failure_type:
            payload["failure_type"] = self.failure_type
        if self.error_code:
            payload["error_code"] = self.error_code
        return payload


@dataclass(frozen=True)
class StageExecutionResult:
    ok: bool
    inner: dict[str, Any] = field(default_factory=dict)
    stage_name: str = ""
    attempted_stages: tuple[str, ...] = ()
    stage_failures: tuple[StageFailure, ...] = ()
    failure_type: str = ""
    failure_reason: str = ""
    error_code: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "inner": dict(self.inner),
            "stage_name": self.stage_name,
            "attempted_stages": list(self.attempted_stages),
            "stage_failures": [item.as_dict() for item in self.stage_failures],
            "failure_type": self.failure_type,
            "failure_reason": self.failure_reason,
            "error_code": self.error_code,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


def _detect_provider_capacity_failure(*, stdout_text: str, stderr_text: str) -> tuple[str, str, str] | None:
    combined = f"{stdout_text}\n{stderr_text}".strip()
    lowered = combined.lower()
    if not any(pattern in lowered for pattern in _PROVIDER_CAPACITY_PATTERNS):
        return None
    excerpt = combined.replace("\r", " ").replace("\n", " ").strip()[:220]
    return ("ENVIRONMENT_FAILED", "PROVIDER_QUOTA_EXHAUSTED", f"PROVIDER_QUOTA_EXHAUSTED: {excerpt}")


async def record_orchestration_event(
    *,
    db,
    task_id: int | None,
    phase: str,
    stage: str,
    session_id: str,
    status: str,
    summary: str,
    queue_mode: str,
    cfg,
    create_task_orchestration_run: Callable[..., Awaitable[Any]],
) -> None:
    if db is None:
        return
    with contextlib.suppress(Exception):
        await create_task_orchestration_run(
            db,
            task_id=task_id,
            phase=phase,
            stage=stage,
            session_id=session_id,
            runtime=str(getattr(cfg, "OPENCLAW_RUNTIME", "acp") or "acp"),
            queue_mode=queue_mode or str(getattr(cfg, "OPENCLAW_QUEUE_MODE", "require_empty_queue")),
            status=status,
            summary=summary[:500],
        )


async def write_generated_blocks_to_worker(
    *,
    working_dir: str,
    generated_output: str,
    parse_code_blocks: Callable[[str], list[tuple[str, str]]],
    normalize_manifest_path: Callable[[str], str],
    is_safe_relative_path: Callable[[str], bool],
    send_action: Callable[..., Awaitable[dict[str, Any]]],
    action_exit_code: Callable[[dict[str, Any]], int],
    action_error_text: Callable[[dict[str, Any], str], str],
) -> tuple[list[str], str]:
    blocks = parse_code_blocks(generated_output or "")
    if not blocks:
        return [], "No file code blocks found in orchestration output."

    written: list[str] = []
    errors: list[str] = []
    for filename, content in blocks:
        relative = normalize_manifest_path(str(filename or "").strip())
        if not is_safe_relative_path(relative):
            errors.append(f"unsafe path skipped: {filename}")
            continue
        result = await send_action(
            "file_write",
            {"file": f"{working_dir}/{relative}", "content": content},
            timeout=30,
            confirmed=True,
        )
        if result.get("status") == "error" or action_exit_code(result) != 0:
            errors.append(f"{relative}: {action_error_text(result, 'file_write')[:180]}")
            continue
        written.append(relative)
    return written, "; ".join(errors[:4])


async def run_stage_chain_for_generation(
    *,
    deps: StageExecutionDeps,
    db,
    app,
    chat_id: int,
    user_id: int | None,
    project: dict[str, Any],
    task_id: int | None,
    prompt: str,
    working_dir: str,
    stage_chain: list[str],
    label_prefix: str,
    graph_id: str = "",
    node_key: str = "",
    node_type: str = "",
    worker_id: str = "",
    timeout_seconds: int = 1800,
    require_runnable_files: bool = True,
    notify_stage_switch: bool = True,
    tracker_hook: Callable[..., Awaitable[None]] | None = None,
) -> StageExecutionResult:
    attempted_stages: list[str] = []
    stage_failures: list[StageFailure] = []

    if not stage_chain:
        return StageExecutionResult(
            ok=False,
            attempted_stages=tuple(attempted_stages),
            stage_failures=tuple(stage_failures),
            failure_reason="No stages configured.",
        )

    for idx, stage_name in enumerate(stage_chain, start=1):
        attempted_stages.append(stage_name)
        use_acp = deps.use_acp_orchestration()
        stage_session_key = uuid.uuid4().hex
        payload: dict[str, Any] | None = None
        if not use_acp:
            payload = deps.stage_payload(
                stage_name=stage_name,
                prompt=prompt,
                working_dir=working_dir,
                timeout_seconds=timeout_seconds,
                project_id=str(project.get("id") or ""),
                task_id=str(task_id or ""),
                graph_id=graph_id,
                node_key=node_key,
                node_type=node_type,
                worker_id=worker_id,
                session_key=stage_session_key,
            )
        session_id = ""
        queue_mode = str(getattr(deps.cfg, "OPENCLAW_QUEUE_MODE", "require_empty_queue") or "require_empty_queue")
        deps.logger.info(
            "coding.stage.start project_id=%s task_id=%s stage=%s",
            project.get("id"),
            task_id,
            stage_name,
        )
        await deps.emit_runtime_trace_async(
            db=db,
            event="coding.stage.start",
            status="start",
            flow=deps.runtime_flow(),
            project_id=str(project.get("id") or ""),
            task_id=str(task_id or ""),
            graph_id=str(graph_id or ""),
            node_key=str(node_key or ""),
            node_type=str(node_type or ""),
            phase=str(label_prefix or ""),
            stage=stage_name,
            worker_id=str(worker_id or getattr(deps.cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "") or ""),
            transport=deps.runtime_transport_label(use_acp=use_acp),
            runtime_mode=deps.runtime_mode_label(use_acp=use_acp),
            action_name="run_coding_agent",
            command_hash=deps.command_hash(prompt),
            working_dir=working_dir,
            session_key=stage_session_key,
            details={"stage_index": idx, "stage_total": len(stage_chain)},
        )
        if use_acp:
            await deps.record_orchestration_event(
                db=db,
                task_id=task_id,
                phase=label_prefix,
                stage=stage_name,
                session_id="pending",
                status="started",
                summary=f"Starting orchestration stage {stage_name}",
                queue_mode=queue_mode,
            )
        if tracker_hook is not None:
            with contextlib.suppress(Exception):
                await tracker_hook(
                    event="stage_start",
                    stage=stage_name,
                    stage_index=idx,
                    stage_total=len(stage_chain),
                    runtime=(str(getattr(deps.cfg, "OPENCLAW_RUNTIME", "acp") or "acp") if use_acp else ""),
                    queue_mode=(queue_mode if use_acp else ""),
                    detail=f"Running stage {stage_name} ({idx}/{len(stage_chain)})",
                )

        failure_reason = ""
        failure_type = ""
        error_code = ""
        result: dict[str, Any] | None = None
        inner: dict[str, Any] = {}
        return_code = 1
        written: list[str] = []
        try:
            heartbeat_runtime = (
                str(getattr(deps.cfg, "OPENCLAW_RUNTIME", "acp") or "acp")
                if use_acp
                else deps.runtime_mode_label(use_acp=False)
            )
            heartbeat_queue_mode = queue_mode if use_acp else ""

            async def _heartbeat_tracker(elapsed: int, *, sid: str = "") -> None:
                if tracker_hook is None:
                    return
                await tracker_hook(
                    event="stage_heartbeat",
                    stage=stage_name,
                    stage_index=idx,
                    stage_total=len(stage_chain),
                    elapsed=elapsed,
                    session_id=sid,
                    runtime=heartbeat_runtime,
                    queue_mode=heartbeat_queue_mode,
                    detail=f"Stage {stage_name} still running ({elapsed}s)",
                )

            if use_acp:
                runner = deps.get_openclaw_runner()
                session = await runner.start_session(
                    phase=label_prefix,
                    project_id=str(project.get("id") or ""),
                    task_id=task_id,
                    stage=deps.acp_stage_name(stage_name),
                    runtime=str(getattr(deps.cfg, "OPENCLAW_RUNTIME", "acp") or "acp"),
                    queue_mode=queue_mode,
                )
                session_id = str(session.get("session_id") or "")
                if session_id:
                    await deps.record_orchestration_event(
                        db=db,
                        task_id=task_id,
                        phase=label_prefix,
                        stage=stage_name,
                        session_id=session_id,
                        status="running",
                        summary=f"session started ({stage_name})",
                        queue_mode=queue_mode,
                    )

                stage_prompt = (
                    "Control-plane coding mode:\n"
                    "- You cannot edit worker files directly.\n"
                    "- Return every file as fenced code blocks tagged with exact filenames.\n"
                    "- Do not add non-code explanations outside file blocks.\n\n"
                    f"{prompt}"
                )
                run_task = asyncio.create_task(
                    runner.run_prompt(
                        session_id=session_id,
                        prompt=stage_prompt,
                        timeout_seconds=timeout_seconds,
                        stage=deps.acp_stage_name(stage_name),
                        model=(
                            str(getattr(deps.cfg, "CLAUDE_OLLAMA_DEFAULT_MODEL", "") or "")
                            if stage_name == "claude_ollama"
                            else ""
                        ),
                        backend=deps.acp_backend_name(stage_name),
                    )
                )
                heartbeat = max(1, int(getattr(deps.cfg, "CODING_PROGRESS_HEARTBEAT_SECONDS", 30) or 30))
                max_wait_seconds = max(1, int(getattr(deps.cfg, "CODING_AGENT_MAX_WAIT_SECONDS", 900) or 900))
                elapsed = 0
                run_result: dict[str, Any] | None = None
                while True:
                    try:
                        run_result = await asyncio.wait_for(asyncio.shield(run_task), timeout=heartbeat)
                        break
                    except asyncio.TimeoutError:
                        elapsed += heartbeat
                        if user_id is not None and app.bot_data.get(deps.stop_request_key(user_id)):
                            await runner.cancel(session_id)
                            raise RuntimeError("STOP_REQUESTED: session stop requested by user")
                        if elapsed >= max_wait_seconds:
                            await runner.cancel(session_id)
                            raise RuntimeError(
                                f"WAIT_TIMEOUT: {label_prefix} via {stage_name} exceeded {max_wait_seconds}s"
                            )
                        await app.bot.send_message(
                            chat_id,
                            f"\u23f3 Still working on {label_prefix} via {stage_name} ({elapsed}s elapsed)...",
                        )
                        await _heartbeat_tracker(elapsed, sid=session_id)

                run_result = run_result or {"returncode": 1, "stdout": "", "stderr": "unknown orchestration error"}
                generated_output = str(run_result.get("stdout") or "")
                written, write_errors = await deps.write_generated_blocks_to_worker(
                    working_dir=working_dir,
                    generated_output=generated_output,
                )
                stderr_text = str(run_result.get("stderr") or "").strip()
                if write_errors:
                    stderr_text = f"{stderr_text}\nFILE_WRITE_WARNINGS: {write_errors}".strip()
                inner = {
                    "returncode": int(run_result.get("returncode", 1) or 1),
                    "stdout": generated_output,
                    "stderr": stderr_text,
                    "files_written": written,
                    "session_id": session_id,
                    "runtime": str(getattr(deps.cfg, "OPENCLAW_RUNTIME", "acp") or "acp"),
                    "queue_mode": queue_mode,
                }
                result = {"status": "success", "result": inner}
            else:
                result = await deps.send_action_with_heartbeat(
                    app=app,
                    chat_id=chat_id,
                    user_id=user_id,
                    action="run_coding_agent",
                    params=payload or {},
                    timeout=timeout_seconds,
                    label=f"{label_prefix} via {stage_name} ({idx}/{len(stage_chain)})",
                    max_wait_seconds=max(1, int(getattr(deps.cfg, "CODING_AGENT_MAX_WAIT_SECONDS", 900) or 900)),
                    heartbeat_hook=lambda elapsed: _heartbeat_tracker(elapsed, sid=""),
                )
        except Exception as exc:
            reason = str(exc).strip()
            if reason.startswith("STOP_REQUESTED:"):
                raise
            if reason.startswith("WAIT_TIMEOUT:"):
                failure_reason = reason
                return_code = 124
            else:
                failure_reason = f"{type(exc).__name__}: {exc}"
        else:
            inner = deps.action_inner_result(result or {})
            return_code = int(inner.get("returncode", inner.get("exit_code", 0)) or 0)
            written = deps.normalize_written_files(inner.get("files_written"))

            # If agent returned success but no files, try extracting code blocks
            # from stdout and writing them to the worker (mirrors the ACP path).
            if return_code == 0 and not deps.has_runnable_written_files(written):
                generated_output = str(inner.get("stdout") or "")
                if generated_output.strip():
                    recovered_files, _ = await deps.write_generated_blocks_to_worker(
                        working_dir=working_dir,
                        generated_output=generated_output,
                    )
                    if recovered_files:
                        written = deps.normalize_written_files(recovered_files)

            capacity_failure = _detect_provider_capacity_failure(
                stdout_text=str(inner.get("stdout") or ""),
                stderr_text=str(inner.get("stderr") or ""),
            )
            if capacity_failure is not None:
                failure_type, error_code, failure_reason = capacity_failure
            elif result.get("status") == "error":
                failure_reason = deps.action_error_text(result, "run_coding_agent")
            elif return_code != 0:
                failure_reason = deps.action_excerpt(result)
            elif require_runnable_files and not deps.has_runnable_written_files(written):
                existing_runnable_project = False
                with contextlib.suppress(Exception):
                    existing_runnable_project = bool(
                        await deps.working_dir_has_valid_run_contract(working_dir=working_dir)
                    )
                if not existing_runnable_project:
                    failure_reason = "no runnable files generated"

        if not failure_reason:
            deps.logger.info(
                "coding.stage.success project_id=%s task_id=%s stage=%s returncode=%s files=%s",
                project.get("id"),
                task_id,
                stage_name,
                return_code,
                len(written),
            )
            if use_acp:
                await deps.record_orchestration_event(
                    db=db,
                    task_id=task_id,
                    phase=label_prefix,
                    stage=stage_name,
                    session_id=session_id or str(inner.get("session_id") or ""),
                    status="passed",
                    summary=f"rc={return_code} files={len(written)}",
                    queue_mode=queue_mode,
                )
            if tracker_hook is not None:
                with contextlib.suppress(Exception):
                    await tracker_hook(
                        event="stage_success",
                        stage=stage_name,
                        stage_index=idx,
                        stage_total=len(stage_chain),
                        returncode=return_code,
                        files_written=len(written),
                        session_id=session_id or str(inner.get("session_id") or ""),
                        runtime=str(inner.get("runtime") or ""),
                        queue_mode=str(inner.get("queue_mode") or ""),
                        detail=f"Stage {stage_name} succeeded",
                    )
            await deps.emit_runtime_trace_async(
                db=db,
                event="coding.stage.end",
                status="ok",
                flow=deps.runtime_flow(),
                project_id=str(project.get("id") or ""),
                task_id=str(task_id or ""),
                graph_id=str(graph_id or ""),
                node_key=str(node_key or ""),
                node_type=str(node_type or ""),
                phase=str(label_prefix or ""),
                stage=stage_name,
                worker_id=str(worker_id or getattr(deps.cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "") or ""),
                transport=deps.runtime_transport_label(use_acp=use_acp),
                runtime_mode=deps.runtime_mode_label(use_acp=use_acp),
                action_name="run_coding_agent",
                command_hash=deps.command_hash(prompt),
                working_dir=working_dir,
                session_key=stage_session_key,
                details={"returncode": return_code, "files_written": len(written)},
            )
            return StageExecutionResult(
                ok=True,
                inner=inner,
                stage_name=stage_name,
                attempted_stages=tuple(attempted_stages),
                stage_failures=tuple(stage_failures),
            )

        failure_excerpt = str(failure_reason).strip()[:220] or "unknown stage failure"
        deps.logger.warning(
            "coding.stage.fail project_id=%s task_id=%s stage=%s returncode=%s error_excerpt=%s",
            project.get("id"),
            task_id,
            stage_name,
            return_code,
            failure_excerpt,
        )
        await deps.emit_runtime_trace_async(
            db=db,
            event="coding.stage.end",
            status="fail",
            level="error",
            flow=deps.runtime_flow(),
            project_id=str(project.get("id") or ""),
            task_id=str(task_id or ""),
            graph_id=str(graph_id or ""),
            node_key=str(node_key or ""),
            node_type=str(node_type or ""),
            phase=str(label_prefix or ""),
            stage=stage_name,
            worker_id=str(worker_id or getattr(deps.cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "") or ""),
            transport=deps.runtime_transport_label(use_acp=use_acp),
            runtime_mode=deps.runtime_mode_label(use_acp=use_acp),
            error_type="StageFailure",
            error_code=error_code or "GENERATION_FAILED",
            error_message=failure_excerpt,
            action_name="run_coding_agent",
            command_hash=deps.command_hash(prompt),
            working_dir=working_dir,
            session_key=stage_session_key,
            failure_class=failure_type or "GENERATION_FAILED",
            mitigation_hint="Inspect stage output and causal chain via /trace deep.",
            details={"returncode": return_code, "attempted_stages": list(attempted_stages)},
            debug_bundle=deps.build_debug_bundle(
                failure_class=failure_type or "GENERATION_FAILED",
                error_message=failure_excerpt,
                causal_chain=[f"coding.stage.start:{stage_name}", f"coding.stage.end:{stage_name}"],
                last_success_event=(f"coding.stage.end:{attempted_stages[-2]}" if len(attempted_stages) > 1 else ""),
                stdout_tail=str(inner.get("stdout") or ""),
                stderr_tail=str(inner.get("stderr") or failure_excerpt),
                files_touched=[str(path) for path in written if str(path).strip()],
                mitigation_hint="Validate worker writability, prompt quality, and command output.",
                retry_policy_snapshot={"stage_index": idx, "stage_total": len(stage_chain)},
            ),
        )
        if use_acp:
            await deps.record_orchestration_event(
                db=db,
                task_id=task_id,
                phase=label_prefix,
                stage=stage_name,
                session_id=session_id or str(inner.get("session_id") or ""),
                status="failed",
                summary=failure_excerpt,
                queue_mode=queue_mode,
            )
        stage_failures.append(
            StageFailure(
                stage=stage_name,
                returncode=str(return_code),
                error_excerpt=failure_excerpt,
                failure_type=failure_type,
                error_code=error_code,
            )
        )
        if tracker_hook is not None:
            with contextlib.suppress(Exception):
                await tracker_hook(
                    event="stage_fail",
                    stage=stage_name,
                    stage_index=idx,
                    stage_total=len(stage_chain),
                    returncode=return_code,
                    reason=failure_excerpt,
                    session_id=session_id or str(inner.get("session_id") or ""),
                    runtime=str(inner.get("runtime") or ""),
                    queue_mode=str(inner.get("queue_mode") or ""),
                    detail=f"Stage {stage_name} failed: {failure_excerpt}",
                )
        if notify_stage_switch and idx < len(stage_chain):
            next_stage = stage_chain[idx]
            if tracker_hook is not None:
                with contextlib.suppress(Exception):
                    await tracker_hook(
                        event="stage_switch",
                        stage=stage_name,
                        next_stage=next_stage,
                        stage_index=idx,
                        stage_total=len(stage_chain),
                        reason=failure_excerpt,
                        detail=f"Switching from {stage_name} to {next_stage}",
                    )
            await app.bot.send_message(
                chat_id,
                f"\u26A0\uFE0F Stage {stage_name} failed ({failure_excerpt}). Trying {next_stage}...",
            )

    last_failure = stage_failures[-1] if stage_failures else None
    return StageExecutionResult(
        ok=False,
        inner={},
        stage_name="",
        attempted_stages=tuple(attempted_stages),
        stage_failures=tuple(stage_failures),
        failure_type=(last_failure.failure_type if last_failure is not None else ""),
        failure_reason=(last_failure.error_excerpt if last_failure is not None else ""),
        error_code=(last_failure.error_code if last_failure is not None else ""),
    )
