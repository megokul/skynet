from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import time
import uuid
from typing import Any

import gateway_config as cfg


class OpenClawRunner:
    """
    Lightweight OpenClaw orchestration adapter.

    This adapter models ACP sessions and runs agent CLIs on the control plane.
    It keeps an in-memory trace per session so higher layers can expose progress.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def start_session(
        self,
        phase: str,
        project_id: str,
        task_id: int | None,
        stage: str,
        runtime: str,
        queue_mode: str,
    ) -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        now = time.time()
        state = {
            "session_id": session_id,
            "phase": str(phase or "").strip(),
            "project_id": str(project_id or "").strip(),
            "task_id": int(task_id) if isinstance(task_id, int) else None,
            "stage": str(stage or "").strip(),
            "runtime": str(runtime or cfg.OPENCLAW_RUNTIME or "acp").strip(),
            "queue_mode": str(queue_mode or cfg.OPENCLAW_QUEUE_MODE or "require_empty_queue").strip(),
            "status": "started",
            "started_at": now,
            "updated_at": now,
            "events": [],
            "task": None,
            "result": None,
        }
        self._append_event(
            state,
            "session.start",
            summary=f"phase={state['phase']} stage={state['stage']} runtime={state['runtime']}",
        )
        async with self._lock:
            self._sessions[session_id] = state
        return {
            "session_id": session_id,
            "phase": state["phase"],
            "stage": state["stage"],
            "runtime": state["runtime"],
            "queue_mode": state["queue_mode"],
            "status": state["status"],
        }

    async def run_prompt(
        self,
        session_id: str,
        prompt: str,
        timeout_seconds: int,
        *,
        stage: str | None = None,
        model: str = "",
        backend: str = "native",
    ) -> dict[str, Any]:
        state = await self._require_session(session_id)
        stage_name = str(stage or state.get("stage") or "").strip().lower()
        timeout = max(30, min(int(timeout_seconds or 1800), 3600))

        self._append_event(
            state,
            "session.run.start",
            summary=f"stage={stage_name} timeout={timeout}s backend={backend}",
        )
        state["status"] = "running"
        state["updated_at"] = time.time()

        async def _execute() -> dict[str, Any]:
            return await self._run_stage_prompt(
                stage=stage_name,
                prompt=prompt,
                timeout_seconds=timeout,
                model=model,
                backend=backend,
            )

        task = asyncio.create_task(_execute())
        state["task"] = task
        try:
            result = await asyncio.wait_for(task, timeout=timeout + 5)
        except asyncio.CancelledError:
            state["status"] = "cancelled"
            state["updated_at"] = time.time()
            self._append_event(state, "session.cancelled", summary="run cancelled")
            raise
        except asyncio.TimeoutError:
            result = {
                "returncode": 124,
                "stdout": "",
                "stderr": f"TIMEOUT: stage {stage_name} exceeded {timeout}s",
            }
        except Exception as exc:  # pragma: no cover - defensive
            result = {
                "returncode": 1,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
            }
        finally:
            state["task"] = None

        state["result"] = result
        state["status"] = "done" if int(result.get("returncode", 1) or 1) == 0 else "failed"
        state["updated_at"] = time.time()
        self._append_event(
            state,
            "session.run.done",
            summary=(
                f"stage={stage_name} rc={int(result.get('returncode', 1) or 1)} "
                f"stderr={str(result.get('stderr') or '')[:180]}"
            ),
        )
        return result

    async def wait(self, session_id: str, timeout_seconds: int) -> dict[str, Any]:
        state = await self._require_session(session_id)
        task = state.get("task")
        if not isinstance(task, asyncio.Task):
            return dict(state.get("result") or {"returncode": 1, "stdout": "", "stderr": "No active task."})
        timeout = max(1, int(timeout_seconds or 1))
        result = await asyncio.wait_for(task, timeout=timeout)
        return result if isinstance(result, dict) else {"returncode": 1, "stdout": "", "stderr": "Invalid task result."}

    async def cancel(self, session_id: str) -> bool:
        state = await self._require_session(session_id)
        task = state.get("task")
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()
            state["status"] = "cancelled"
            state["updated_at"] = time.time()
            self._append_event(state, "session.cancel.requested", summary="cancel requested")
            return True
        return False

    async def collect_trace(self, session_id: str) -> list[dict[str, Any]]:
        state = await self._require_session(session_id)
        events = state.get("events", [])
        if isinstance(events, list):
            return [dict(item) for item in events if isinstance(item, dict)]
        return []

    def available_stages(self, requested_chain: list[str]) -> tuple[list[str], dict[str, str]]:
        available: list[str] = []
        reasons: dict[str, str] = {}
        for stage in requested_chain:
            binary = self._resolve_binary(self._stage_to_binary_name(stage))
            if binary:
                available.append(stage)
                continue
            reasons[stage] = f"missing binary: {self._stage_to_binary_name(stage)}"
        return available, reasons

    async def _require_session(self, session_id: str) -> dict[str, Any]:
        sid = str(session_id or "").strip()
        async with self._lock:
            state = self._sessions.get(sid)
        if not isinstance(state, dict):
            raise RuntimeError(f"Unknown orchestration session: {sid or 'empty'}")
        return state

    def _append_event(self, state: dict[str, Any], event: str, *, summary: str = "") -> None:
        state.setdefault("events", [])
        state["events"].append(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "event": str(event),
                "summary": str(summary or ""),
            }
        )

    async def _run_stage_prompt(
        self,
        *,
        stage: str,
        prompt: str,
        timeout_seconds: int,
        model: str,
        backend: str,
    ) -> dict[str, Any]:
        binary_name = self._stage_to_binary_name(stage)
        resolved = self._resolve_binary(binary_name)
        if not resolved:
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": f"OPENCLAW_SETUP_ERROR: '{binary_name}' CLI not found on control plane PATH.",
            }

        args = self._build_command(
            stage=stage,
            binary=resolved,
            prompt=prompt,
            model=model,
        )
        env = os.environ.copy()
        if stage in {"claude", "claude_ollama"} and backend == "ollama":
            env.update(
                {
                    "ANTHROPIC_AUTH_TOKEN": cfg.CLAUDE_OLLAMA_AUTH_TOKEN or "ollama",
                    "ANTHROPIC_API_KEY": "",
                    "ANTHROPIC_BASE_URL": cfg.CLAUDE_OLLAMA_BASE_URL,
                }
            )

        return await self._run_subprocess(args=args, timeout_seconds=timeout_seconds, env=env)

    async def _run_subprocess(
        self,
        *,
        args: list[str],
        timeout_seconds: int,
        env: dict[str, str],
    ) -> dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            with contextlib.suppress(Exception):
                proc.kill()
            return {
                "returncode": 124,
                "stdout": "",
                "stderr": f"TIMEOUT: command exceeded {timeout_seconds}s",
            }
        return {
            "returncode": int(proc.returncode or 0),
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }

    def _resolve_binary(self, configured: str) -> str:
        candidate = str(configured or "").strip()
        if not candidate:
            return ""
        if os.path.isabs(candidate):
            return candidate if os.path.exists(candidate) else ""
        return shutil.which(candidate) or ""

    def _stage_to_binary_name(self, stage: str) -> str:
        mapped = {
            "codex": cfg.OPENCLAW_CODEX_BIN,
            "claude": cfg.OPENCLAW_CLAUDE_BIN,
            "claude_ollama": cfg.OPENCLAW_CLAUDE_BIN,
            "cline": cfg.OPENCLAW_CLINE_BIN,
        }
        return str(mapped.get(str(stage or "").strip().lower()) or "")

    def _build_command(
        self,
        *,
        stage: str,
        binary: str,
        prompt: str,
        model: str,
    ) -> list[str]:
        stage_name = str(stage or "").strip().lower()
        if stage_name == "codex":
            # Codex may run from non-git working dirs during milestone generation.
            return [binary, "exec", "--skip-git-repo-check", prompt]
        if stage_name in {"claude", "claude_ollama"}:
            args = [binary]
            selected_model = str(model or "").strip()
            if selected_model:
                args.extend(["--model", selected_model])
            args.extend(["-p", prompt])
            return args
        if stage_name == "cline":
            return [binary, "-p", prompt]
        return [binary, "-p", prompt]


_RUNNER: OpenClawRunner | None = None


def get_openclaw_runner() -> OpenClawRunner:
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = OpenClawRunner()
    return _RUNNER

