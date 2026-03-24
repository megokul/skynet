from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class PytestLiveFlowSpec:
    flow: str
    target: str
    step: str
    invoke_event: str
    exit_event: str
    subprocess_label: str
    success_banner: str
    failure_message: str
    strict_skip_message: str
    infer_infra_category: bool = False


def summarize_telegram_tracker_output(output: str, trace_fn: Callable[..., None]) -> None:
    tracker_detected = len(re.findall(r'"event"\s*:\s*"tracker\.message\.detected"', output))
    tracker_edited = len(re.findall(r'"event"\s*:\s*"tracker\.message\.edited"', output))
    if tracker_detected or tracker_edited:
        trace_fn(
            "telegram_real.tracker.summary",
            tracker_detected=tracker_detected,
            tracker_edited=tracker_edited,
        )


def run_pytest_live_flow(
    *,
    trace,
    cleanup,
    spec: PytestLiveFlowSpec,
    repo_root: str,
    build_env: Callable[[dict[str, str]], dict[str, str]],
    apply_policy_env: Callable[[dict[str, str], str], dict[str, str]],
    run_subprocess_with_cleanup: Callable[..., subprocess.CompletedProcess[str]],
    fail_fn: Callable[..., None],
    strict_skip: bool,
    infer_infra_category_fn: Callable[[str], str] | None = None,
    output_observer: Callable[[str, Callable[..., None]], None] | None = None,
) -> None:
    cmd = [sys.executable, "-m", "pytest", spec.target, "-q", "-s"]
    env = build_env(dict(os.environ))
    env["SKYNET_E2E_LIVE"] = os.environ.get("SKYNET_E2E_LIVE", "1")
    env["SKYNET_LIVE_TRACE_FILE"] = str(trace.path)
    env = apply_policy_env(env, spec.flow)

    trace.log("e2e.step.start", step=spec.step, status="start")
    trace.log(spec.invoke_event, repo_root=repo_root, cmd=" ".join(cmd))
    completed = run_subprocess_with_cleanup(
        cmd=cmd,
        env=env,
        cleanup=cleanup,
        label=spec.subprocess_label,
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="" if completed.stderr.endswith("\n") else "\n")

    output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    if output_observer is not None:
        output_observer(output, trace.log)
    match_passed = re.search(r"(\d+)\s+passed", output, flags=re.IGNORECASE)
    match_skipped = re.search(r"(\d+)\s+skipped", output, flags=re.IGNORECASE)
    passed = int(match_passed.group(1)) if match_passed else 0
    skipped = int(match_skipped.group(1)) if match_skipped else 0
    exit_fields: dict[str, Any] = {
        "returncode": completed.returncode,
        "pytest_passed": passed,
        "pytest_skipped": skipped,
    }
    infra_category = ""
    if spec.infer_infra_category and infer_infra_category_fn is not None:
        infra_category = infer_infra_category_fn(output)
        exit_fields["infra_category"] = infra_category
    trace.log(spec.exit_event, **exit_fields)

    if completed.returncode == 0 and skipped > 0 and strict_skip:
        if infra_category:
            print(f"[INFRA] Live E2E skip category: {infra_category}")
        trace.log(
            "e2e.step.fail",
            step=spec.step,
            status="fail",
            error_message=spec.strict_skip_message,
        )
        fail_fn(trace, spec.strict_skip_message, detail=f"pytest skipped={skipped}")

    if completed.returncode != 0:
        if infra_category:
            print(f"[INFRA] {spec.flow} live E2E infra category: {infra_category}")
        trace.log(
            "e2e.step.fail",
            step=spec.step,
            status="fail",
            error_message=f"{spec.failure_message} (exit={completed.returncode}).",
        )
        fail_fn(trace, spec.failure_message, detail=f"pytest exit code: {completed.returncode}")

    trace.log("e2e.step.end", step=spec.step, status="ok")
    trace.log("run.success", flow=spec.flow)
    print(spec.success_banner)
    print(f"[TRACE] {trace.path}")
