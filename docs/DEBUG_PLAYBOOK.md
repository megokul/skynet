# SKYNET Debug Playbook

Triage-first operational playbook for runtime issues.

## Triage Workflow

1. Identify mode: WS worker mode vs SSH tunnel fallback.
2. Capture identifiers: `request_id`, `task_id`, `claim_token`, timestamp, environment mode.
3. Verify service health and connectivity endpoints.
4. Inspect task/event/audit trails before patching behavior.
5. Reproduce with smallest command/test path and keep evidence in handoff.

## Live E2E Debug Policy (Mandatory Loop)

Use this policy for every `telegram_real` failure. Do not stop at first symptom.

1. Run live E2E once and capture:
   - trace file path
   - failing step name
   - last bot message
   - project slug/folder
2. Extract root cause evidence before any fix:
   - gateway stacktrace for the same timestamp window
   - relevant `/status` or `/trace` output
   - task/event rows for the affected project/task
   - write a clear bug explanation for operators: `symptom -> root cause -> impact`
3. Classify failure category (single primary category):
   - `ENV_MISCONFIG`
   - `SSH_INFRA`
   - `PLANNER_PARSE`
   - `MILESTONE_EXTRACTION`
   - `GENERATION`
   - `STRICT_GATES`
   - `LOOP_CRASH`
   - `TEST_HARNESS`
4. Write a mitigation plan (required before edits):
   - root cause statement
   - why this fix is durable (not a quick patch)
   - files to change
   - regression tests to add/update
   - rollback behavior
5. Implement fix + tests.
6. Commit and push before rerunning live E2E:
   - create commit for the debug cycle
   - push must succeed on remote branch
   - if push fails, resolve push failure first (do not run live E2E yet)
7. Re-run live E2E and verify deliverables:
   - coding reaches completion path
   - generated files exist in project folder
   - `skynet_run.json` is present and valid
   - run action exits `0`
8. If fail again, repeat from step 2 with a new mitigation plan version (`v2`, `v3`, ...).

### Completion Rule (Do Not Close Early)

A live E2E incident is only considered resolved when all are true:

1. Latest live E2E run passes end-to-end (`hi -> project -> coding -> run`).
2. Generated project artifacts exist on disk (not only chat status).
3. `skynet_run.json` is valid and run exits `0`.
4. Debug-cycle commit exists and remote push succeeded before the passing live E2E run.
5. Failure category from previous run has mitigation evidence and regression coverage.
6. Handoff includes trace links for the failing run and the fixed run.

### Mitigation Plan Template

Use this exact structure in debug notes/handoff:

1. `Failure`: one-line symptom from trace.
2. `Root Cause`: concrete exception or deterministic behavior.
3. `Bug Explanation`: short operator-facing summary (`symptom -> cause -> impact`).
4. `Impact`: what user-visible behavior breaks.
5. `Fix Plan`: numbered code changes.
6. `Validation`: tests + live E2E command and expected outputs.
7. `Result`: pass/fail with artifact links.

## Connectivity Failures

Gateway <-> control plane:

```bash
curl -fsS http://127.0.0.1:8000/v1/health
curl -fsS http://127.0.0.1:8000/v1/system-state
```

Gateway worker path:

```bash
curl -fsS http://127.0.0.1:8766/status
```

Interpretation:

- `agent_connected=true`: WS worker path active.
- `ssh_fallback_enabled=true` + healthy target: SSH path active or available.
- `execution_mode=ssh_tunnel`: forced SSH mode.
- `/status` diagnostics:
  - `ssh_error_category`: `unreachable|auth|capacity|banner|timeout|unknown`
  - `ssh_failure_streak`: consecutive infra failures
  - `ssh_circuit_open_until`: epoch when retries resume
  - `ssh_endpoint`: active host:port target

SSH infra triage quick checks:

```bash
# From gateway container context (authoritative for Docker runtime):
python - <<'PY'
import json, urllib.request
print(json.loads(urllib.request.urlopen("http://localhost:8766/status").read().decode()))
PY
```

Capacity-specific triage (`MaxStartups`):

1. Check `/status` for `ssh_error_category=capacity`.
2. Inspect laptop OpenSSH Operational log for `Exceeded MaxStartups`.
3. Confirm gateway is not opening excessive parallel SSH sessions.
4. Verify laptop `sshd_config` tuning (`MaxStartups`, `MaxSessions`, `LoginGraceTime`).

Banner failure triage:

1. Confirm reverse tunnel process is single-instance (`OpenClawReverseTunnel` task).
2. Verify EC2 bind target is reachable from gateway container (`host.docker.internal:2222` for Docker mode).
3. Run `scripts/check_tunnel_health.ps1` on laptop.
4. If repeated, inspect packet resets / stale ssh.exe process buildup.

## Task Lock and Stale Claim Failures

Symptoms:

- tasks stuck in `claimed` or `running`
- repeated requeues
- conflicting file ownership claims

Checks:

```bash
curl -fsS "http://127.0.0.1:8000/v1/tasks?status=claimed"
curl -fsS "http://127.0.0.1:8000/v1/events?limit=200"
curl -fsS http://127.0.0.1:8000/v1/files/ownership
```

Focus on:

- stale `locked_at`
- missing/invalid `claim_token`
- reaper behavior (`failed_timeout` vs release)

## Idempotency Anomalies

Gateway idempotency applies when both `task_id` and `idempotency_key` are present.

Symptoms:

- duplicate execution for same task claim
- replay not returned for repeated dispatch

Checks:

1. Confirm scheduler sends `task_id` + `claim_token` to gateway client.
2. Confirm gateway `/action` payload includes `idempotency_key`.
3. Confirm response includes `idempotent_replay=true` on duplicate request.

## Provider Routing Failures

Symptoms:

- bot replies indicate provider unavailability
- repeated fallback/cooldown behavior

Checks:

- review configured provider/env variables
- inspect gateway startup logs for provider registration
- run gateway tests to validate local handler flow independently of remote APIs

## Log and Trace Collection

Control-plane:

- `/v1/health`, `/v1/events`, `/v1/tasks`, `/v1/agents`

Gateway:

- `/status`, `/action` responses
- configured trace mirrors (`skynet.trace.log` via SSH/S3 when enabled)

Agent:

- `openclaw-agent/logs/audit.jsonl`

Trace evidence examples:

- include `request_id=...`
- include `task_id=...` and `claim_token=...`
- include path reference to `audit.jsonl` or `skynet.trace.log`
- include relevant `/v1/events` snippet timestamp

## Incident Checklist

- Timestamp in UTC.
- Runtime mode (`agent_preferred` or `ssh_tunnel`).
- `request_id` (if available).
- `task_id` (if queue/scheduler related).
- `claim_token` (if claimed/running/released issue).
- Endpoint results (`/v1/health`, `/v1/events`, gateway `/status`).
- Files touched and tests run during fix.
