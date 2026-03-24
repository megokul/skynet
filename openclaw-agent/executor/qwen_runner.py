from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from skynet.qwen_cli import (
    build_qwen_command_args,
    build_qwen_runtime_prompt,
    build_qwen_subprocess_env,
    load_qwen_execution_policy,
    qwen_profile_for_task_mode,
)
from skynet.qwen_runtime import normalize_qwen_result, normalize_qwen_task_mode


SubprocessRunner = Callable[..., Awaitable[dict[str, Any]]]
GetString = Callable[[str, str], str]
GetBool = Callable[[str, bool], bool]


def get_qwen_execution_policy(*, get_str: GetString, get_bool: GetBool) -> dict[str, Any]:
    return load_qwen_execution_policy(get_str=get_str, get_bool=get_bool)


@contextlib.contextmanager
def managed_qwen_context_file(*, working_dir: str | None, context_text: str, enabled: bool) -> Any:
    text = str(context_text or "").strip()
    cwd = str(working_dir or "").strip()
    if not enabled or not text or not cwd:
        yield None
        return

    os.makedirs(cwd, exist_ok=True)
    context_path = Path(cwd) / "QWEN.md"
    had_existing = context_path.exists()
    previous_text = ""
    if had_existing:
        try:
            previous_text = context_path.read_text(encoding="utf-8")
        except OSError:
            previous_text = ""
    context_path.write_text(text, encoding="utf-8")
    try:
        yield str(context_path)
    finally:
        try:
            if had_existing:
                context_path.write_text(previous_text, encoding="utf-8")
            else:
                context_path.unlink(missing_ok=True)
        except OSError:
            pass


@contextlib.contextmanager
def managed_qwen_working_dir(
    *,
    requested_working_dir: str | None,
    session_key: str,
    profile: dict[str, Any],
) -> Any:
    requested = str(requested_working_dir or "").strip()
    strategy = str(profile.get("working_dir_strategy") or "project").strip().lower() or "project"
    if strategy != "request_scoped":
        if requested:
            os.makedirs(requested, exist_ok=True)
        yield requested or None
        return

    scoped_root = Path(tempfile.gettempdir()) / f"qwen-{profile.get('profile_key', 'planner')}-{session_key or uuid.uuid4().hex}"
    scoped_root.mkdir(parents=True, exist_ok=True)
    try:
        yield str(scoped_root)
    finally:
        shutil.rmtree(scoped_root, ignore_errors=True)


async def run_qwen_agent(
    *,
    params: dict[str, Any],
    resolved_binary: str,
    cwd: str | None,
    timeout: int,
    session_key: str,
    model: str,
    get_str: GetString,
    get_bool: GetBool,
    run_subprocess: SubprocessRunner,
    binary_prefix_args: list[str] | None = None,
) -> dict[str, Any]:
    task_mode = normalize_qwen_task_mode(str(params.get("task_mode") or ""))
    qwen_policy = get_qwen_execution_policy(get_str=get_str, get_bool=get_bool)
    profile = qwen_profile_for_task_mode(qwen_policy, task_mode)
    qwen_context_text = str(params.get("qwen_context_text") or "").strip()
    reply_contract = str(params.get("reply_contract") or "").strip().lower()
    planner_state = params.get("planner_state_json") if isinstance(params.get("planner_state_json"), dict) else {}
    requirement_summary_md = str(params.get("requirement_summary_md") or "").strip()
    effective_model = str(model or profile.get("model") or "").strip()
    runtime_prompt = build_qwen_runtime_prompt(
        prompt=str(params.get("prompt") or ""),
        task_mode=task_mode,
        reply_contract=reply_contract,
        planner_state=planner_state,
        requirement_summary_md=requirement_summary_md,
    )
    args, stdin_prompt = build_qwen_command_args(
        binary=resolved_binary,
        prompt=runtime_prompt,
        session_id=session_key,
        task_mode=task_mode,
        policy=qwen_policy,
        binary_prefix_args=binary_prefix_args,
    )
    qwen_env = build_qwen_subprocess_env(qwen_policy, model=effective_model)
    with managed_qwen_working_dir(
        requested_working_dir=cwd,
        session_key=session_key,
        profile=profile,
    ) as effective_cwd:
        with managed_qwen_context_file(
            working_dir=effective_cwd,
            context_text=qwen_context_text,
            enabled=bool(profile.get("use_context_file", False)) or bool(qwen_context_text),
        ):
            result = await run_subprocess(
                args=args,
                cwd=effective_cwd,
                timeout=timeout,
                session_key=session_key,
                agent="qwen",
                env=qwen_env,
                **({"stdin_data": stdin_prompt} if stdin_prompt else {}),
            )
        result["qwen_requested_working_dir"] = str(cwd or "").strip()
        result["qwen_effective_working_dir"] = str(effective_cwd or "").strip()
        return normalize_qwen_result(
            result,
            task_mode=task_mode,
            policy=qwen_policy,
            working_dir=result.get("qwen_effective_working_dir") or cwd,
            reply_contract=reply_contract,
            planner_state=planner_state,
            requirement_summary_md=requirement_summary_md,
        )
