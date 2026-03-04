# SKYNET Debug Playbook

Triage-first operational playbook for runtime issues.

## Triage Workflow

1. Identify mode: WS worker mode vs SSH tunnel fallback.
2. Capture identifiers: `request_id`, `task_id`, `claim_token`, timestamp, environment mode.
3. Verify service health and connectivity endpoints.
4. Inspect task/event/audit trails before patching behavior.
5. Reproduce with smallest command/test path and keep evidence in handoff.

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
