# SKYNET Control Plane

Authoritative contract: `docs/SKYNET_OPENCLAW_CONTRACT.md`.
Implementation/debug/policy docs: `docs/INDEX.md` (`docs/CODE_QUALITY_STANDARDS.md` is the code-structure baseline for refactors).

- OpenClaw executes tasks.
- SKYNET orchestrates OpenClaw gateways.
- SKYNET does not run agent runtime/tool execution logic.

## WebSocket-First Execution Model

Current worker transport policy:

1. `openclaw-gateway` prefers the connected `openclaw-agent` websocket channel.
2. SSH fallback is available, but disabled by default in the checked-in settings.
3. Gateway runtime traces and `/status` now expose:
   - `primary_transport_mode`
   - websocket heartbeat health
   - fallback readiness/reason
   - websocket log-mirror health

Worker runtime model:

1. EC2 hosts the control plane and gateway services.
2. Worker laptop hosts:
   - `openclaw-agent`
   - `codex` and local toolchain
   - project files
3. The recommended worker lifecycle is the scheduled-task path:
   - `scripts/install_worker_agent.ps1`
   - `scripts/register_worker_agent_task.ps1`
   - `scripts/check_worker_agent_health.ps1`
   - `scripts/repair_worker_agent.ps1`
4. If Windows Scheduled Task registration is blocked by local workstation policy, start the worker agent in a detached user session with:
   - `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_worker_agent.ps1`
   This keeps websocket-primary usable while SSH remains the fallback path.
5. Worker secrets/config should live in `.env.worker-agent` derived from `.env.worker-agent.example`.

Worker bootstrap notes:

1. `scripts/run_worker_agent.ps1` now writes a bootstrap transcript to `logs\worker-agent.bootstrap.log`.
2. The worker env loader no longer aborts on comment lines in `.env.worker-agent`.
3. TCP tunnel probes now avoid the PowerShell `$Host` variable collision that previously killed launcher health checks.

Live E2E websocket expectations:

1. `SKYNET_E2E_REQUIRE_WEBSOCKET_PRIMARY=1` makes the real Telegram E2E fail unless runtime trace shows at least one `gateway.transport.select` event with `transport=websocket_primary`.
2. `SKYNET_E2E_ALLOW_SSH_FALLBACK=1` allows degraded runs to continue when websocket falls back to SSH.
3. The recommended deployment default remains:
   - `OPENCLAW_EXECUTION_MODE=agent_preferred`
   - `SKYNET_CODING_TRANSPORT=websocket_primary`
   - `SKYNET_WEBSOCKET_FALLBACK_TO_SSH=0`
   - `OPENCLAW_SSH_FALLBACK_ENABLED=0`

## Active Scope

`skynet/` now contains control-plane code only:
- Gateway/worker registry
- Health-aware gateway routing
- System topology state
- Authoritative task scheduling and locking (dependency + file ownership aware)

## API Endpoints

- `POST /v1/register-gateway`
- `POST /v1/register-worker`
- `POST /v1/route-task`
- `POST /v1/tasks/enqueue`
- `GET /v1/tasks`
- `GET /v1/tasks/next?agent_id=...`
- `POST /v1/tasks/claim`
- `POST /v1/tasks/{task_id}/start`
- `POST /v1/tasks/{task_id}/complete`
- `POST /v1/tasks/{task_id}/release`
- `GET /v1/agents`
- `GET /v1/events`
- `GET /v1/files/ownership`
- `GET /v1/system-state`
- `GET /v1/health`

## Control-Plane Scheduler Authority

Scheduler authority now lives in `skynet/` (control plane), not in gateway runtime:

- `skynet/ledger/task_queue.py` implements:
  - atomic task lock claim (`locked_by`, `locked_at`, `claim_token`)
  - explicit state machine (`queued -> claimed -> running -> terminal`)
  - strict dependency graph (`dependencies`, `dependents`)
  - file ownership registry (`required_files` + active ownership table)
  - task event stream (`control_task_events`)
- `skynet/control_plane/scheduler.py` runs the scheduling loop:
  - claim ready task
  - transition claimed -> running
  - select gateway
  - dispatch action to gateway with idempotency (`task_id`, `claim_token`)
  - complete/fail/requeue task
- `skynet/control_plane/reaper.py` reaps stale locks:
  - checks worker/gateway health
  - releases stale claims or marks `failed_timeout`

Gateway remains execution transport (`/action`) and does not own scheduling state.

## PostgreSQL Roadmap

SQLite is currently used for control-plane and gateway persistence.
PostgreSQL migration is planned for scale-out deployments (future step).

## Run

```bash
# SKYNET API
make run-api

# OpenClaw runtime (separate)
make run-bot
```

## Docker

```bash
# Full stack (SKYNET + OpenClaw gateway)
docker compose up -d skynet-api openclaw-gateway

# SKYNET only (when OpenClaw already runs elsewhere)
docker compose -f docker-compose.skynet-only.yml up -d
```

## Install

```bash
# Shared control-plane and gateway dependencies
make install

# Worker-agent dependencies
make install-agent

# Both dependency sets
make install-all
```

Dependency ownership:

- root `requirements.txt`: control plane, shared tooling, and gateway test/runtime dependencies
- `openclaw-agent/requirements.txt`: worker-agent specific dependencies

## Config Split: Settings vs Secrets

- Non-secret defaults live in:
  - `skynet/settings/defaults.yaml`
  - `openclaw-gateway/settings/settings.yaml`
  - `openclaw-agent/settings/defaults.yaml`
- Secret values live in environment variables (`.env` or GitHub Actions secrets).
- Runtime precedence is:
  - environment variable
  - settings YAML
  - code default

## Configuration Source Of Truth

- `skynet/settings/loader.py` is the only shared settings loader in the repo.
- Gateway and agent settings loaders are wrappers around it.
- Live E2E, scripts, and deployment env rendering must consume that shared loader instead of parsing `.env` or YAML themselves.
- `scripts/ci/check_settings_policy.py` enforces this policy in CI.

## Sync Local `.env` To GitHub (No Commit)

Use this to sync local secret values to repository Actions secrets:

```bash
python scripts/dev/sync_env_to_github.py --mode secrets
```

Optional stale cleanup (recommended after config trims):

```bash
python scripts/dev/sync_env_to_github.py --mode secrets --prune-stale
```

Safety behavior:
- In `--mode secrets`, only secret-like keys are synced by default.
- Keys with reserved prefix `GITHUB_` are skipped automatically.
- Keys with empty values are skipped automatically.
- Invalid key names are skipped automatically.
- When repo secret quota is full, new secret names are skipped automatically.
- `--prune-stale` preserves keys referenced by workflows and deletes non-kept repo secrets.
- Only actual GitHub API write/delete failures return non-zero.

You can also enable automatic sync on every `git push`:

```bash
git config core.hooksPath .githooks
```

To bypass once:

```bash
SKYNET_SKIP_ENV_SYNC=1 git push
```

## Tests

```bash
make test-control-plane
make test-gateway
make test-agent
make test-policy
make test-all
make smoke
python scripts/ci/check_repo_hygiene.py
python scripts/ci/check_settings_policy.py
python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD
```

Curated root tests are explicit and limited to control-plane and repo-policy coverage:
- `tests/test_api_lifespan.py`
- `tests/test_api_provider_config.py`
- `tests/test_api_control_plane.py`
- `tests/test_job_locking.py`
- `tests/test_task_queue_control_plane.py`
- `tests/test_worker_registry.py`
- `tests/test_ci_engineering_policy.py`
- `tests/test_prompt_references.py`

Default pytest discovery remains focused on:
- `openclaw-gateway/tests/`
- `openclaw-agent/tests/`

Curated root-suite guidance lives in `tests/README.md`.

## Manual Integration

```bash
make manual-check-api
make manual-check-e2e
make manual-check-delegate
```

## External OpenClaw Skills (SKILL.md)

The gateway can load community OpenClaw `SKILL.md` files as prompt guidance.

- Local directory (recursive): `SKYNET_EXTERNAL_SKILLS_DIR`
- Optional remote URLs (comma-separated): `SKYNET_EXTERNAL_SKILL_URLS`

Example:

```bash
export SKYNET_EXTERNAL_SKILL_URLS="https://github.com/openclaw/skills/tree/main/skills/steipete/coding-agent/SKILL.md"
```

This is a prompt-level integration only. Execution remains constrained to built-in allowlisted tools/actions.

## OpenRouter Free Model Priority

When `OPENROUTER_API_KEY` is set, the gateway uses a priority chain and automatically moves to the next model when the current one is unavailable, rate-limited, or quota-exhausted.

| Priority | Model |
| --- | --- |
| 1 | `qwen/qwen3-next-80b-a3b-instruct:free` |
| 2 | `qwen/qwen3-vl-235b-a22b-thinking` |
| 3 | `openai/gpt-oss-120b:free` |
| 4 | `qwen/qwen3-coder:free` |
| 5 | `google/gemma-3n-e2b-it:free` |
| 6 | `deepseek/deepseek-r1-0528:free` |
| 7 | `meta-llama/llama-3.3-70b-instruct:free` |

You can override this chain with `OPENROUTER_MODEL` (primary) and `OPENROUTER_FALLBACK_MODELS` (comma-separated fallback list).

## Multi-API Provider Priority

The router can use all configured APIs and select providers by priority plus task needs.

Default provider priority:
1. `claude`
2. `gemini`
3. `openai`
4. `deepseek`
5. `openrouter`
6. `groq`
7. `ollama`

Override via `AI_PROVIDER_PRIORITY` (comma-separated).

## Autonomous Project Startup

Default behavior now supports end-to-end autonomous project execution:

1. New projects are created under `SKYNET_PROJECT_BASE_DIR` (default `E:\SKYNET-SANDBOX\Projects`).
2. On project creation, the gateway bootstraps:
   - project directory
   - `README.md`
   - `git init` + initial commit
   - optional `gh_create_repo`
3. After enough idea detail (`AUTO_PLAN_MIN_IDEAS`, default `3`), the system can auto:
   - generate plan
   - approve plan
   - start execution
4. Progress updates are posted at milestone boundaries (`milestone_started`, `milestone_review`).

Key env flags:
- `SKYNET_PROJECT_BASE_DIR`
- `AUTO_BOOTSTRAP_PROJECT`
- `AUTO_CREATE_GITHUB_REPO`
- `AUTO_CREATE_GITHUB_PRIVATE`
- `AUTO_APPROVE_AND_START`
- `AUTO_PLAN_MIN_IDEAS`
- `AUTO_APPROVE_GIT_ACTIONS`

## SSH Tunnel Mode (No Worker Install)

If you do not want to install `openclaw-agent` on your laptop, gateway actions can run over SSH instead.

Set these env vars for the gateway:

- `OPENCLAW_EXECUTION_MODE=ssh_tunnel`
- `OPENCLAW_SSH_FALLBACK_ENABLED=1`
- `OPENCLAW_SSH_HOST=<ssh-endpoint>` (for reverse tunnel, usually `127.0.0.1` on EC2 host)
- `OPENCLAW_SSH_PORT=<port>` (example: `2222`)
- `OPENCLAW_SSH_USER=<laptop-ssh-user>`
- `OPENCLAW_SSH_KEY_PATH=<path-to-private-key>` or `OPENCLAW_SSH_PASSWORD=<password>`
- `OPENCLAW_SSH_REMOTE_OS=windows`
- `OPENCLAW_SSH_ALLOWED_ROOTS=E:\MyProjects`

For CI/CD deployments, prefer key auth with GitHub Secrets:

- `OPENCLAW_SSH_PRIVATE_KEY_B64` = base64 of the private key content
- `OPENCLAW_SSH_KEY_PATH=/app/keys/laptop_ed25519`

The workflow will decode the key on the runner and mount it into the gateway container automatically.

Canonical worker tunnel key (laptop scripts):

- `OPENCLAW_TUNNEL_SSH_KEY=E:\MyProjects\skynet-key.pem`
- `OPENCLAW_SSH_KEY_PATH=E:\MyProjects\skynet-key.pem` (fallback for tunnel scripts)

Recommended reverse tunnel from laptop to EC2:

```powershell
ssh -N -R 0.0.0.0:2222:localhost:22 ubuntu@<EC2_PUBLIC_IP>
```

Then point gateway at:

- `OPENCLAW_SSH_HOST=host.docker.internal` (for Dockerized gateway)
- `OPENCLAW_SSH_PORT=2222`

Notes:
- If your SSH server does not allow `0.0.0.0` reverse binds, enable `GatewayPorts clientspecified` on EC2 SSHD.
- If you run gateway directly on the host (not in Docker), you can use `OPENCLAW_SSH_HOST=127.0.0.1`.

Use `/agent_status` in Telegram:

- `Worker Connected` = normal OpenClaw worker mode
- `SSH Tunnel Ready` = tunnel fallback mode active and healthy

### SSH-Primary Profile Split

Use the correct env profile for the runtime context:

- EC2 Dockerized gateway: `.env.ec2-gateway.example`
- Local host-run live E2E: `.env.local-e2e.example`

Mode precedence:

- If `OPENCLAW_EXECUTION_MODE` is `ssh|ssh_tunnel|tunnel|ssh-only`, effective orchestration mode is forced to `legacy`.
- Override only when explicitly needed via `SKYNET_ORCHESTRATION_ALLOW_ACP_WITH_SSH=1`.
- This prevents ACP-local agent checks from running in SSH-primary deployments.

Always run live E2E with an explicit env file:

```powershell
$env:SKYNET_ENV_FILE = ".env.local-e2e"
python openclaw-gateway/tests/e2e_live.py
```

Live E2E flow modes:

- `SKYNET_LIVE_E2E_FLOW=conversation`
  - Handler-path conversation simulation (`hi -> plan -> coding -> run`) with real planner/coding/SSH execution.
- `SKYNET_LIVE_E2E_FLOW=telegram_real`
  - Fully real Telegram-network conversation using a Telethon user session (real messages + inline button clicks).

Live trace logging:

- Default live E2E trace logs are written to `E:\MyProjects\skynet\logs\`.
- Runtime trace source defaults to `E:\MyProjects\skynet\logs\skynet.trace.log`.
- Runtime trace is append-only JSONL with per-event flush for live monitoring.
- Terminal failures emit a mandatory `debug.bundle` event with causal chain and mitigation hint.
- Monitor E2E trace live in PowerShell:

```powershell
Get-Content E:\MyProjects\skynet\logs\e2e-live-*.log -Wait
```

- Override trace file path if needed:
  - `SKYNET_LIVE_TRACE_FILE=E:\MyProjects\skynet\logs\my-live-trace.log`
  - `SKYNET_E2E_RUNTIME_TRACE_FILE=E:\MyProjects\skynet\logs\skynet.trace.log`
  - `SKYNET_RUNTIME_TRACE_LIVE_FILE=E:\MyProjects\skynet\logs\skynet.trace.log`

Runtime trace control flags:

- `SKYNET_E2E_LIVE=1`
- `SKYNET_RUNTIME_TRACE_ENABLED=1`
- `SKYNET_RUNTIME_TRACE_LEVEL=info`
- `SKYNET_RUNTIME_TRACE_PAYLOAD_MODE=redacted_hash`
- `SKYNET_RUNTIME_TRACE_INCLUDE_STDIO_TAIL=1`
- `SKYNET_RUNTIME_TRACE_STDIO_TAIL_BYTES=4000`
- `SKYNET_RUNTIME_TRACE_DB_ENABLED=1`
- `SKYNET_RUNTIME_TRACE_REQUIRE_DEBUG_BUNDLE=1`
- `SKYNET_RUNTIME_TRACE_REQUIRED_EVENT_ENFORCEMENT=1`

Shared live E2E container log flags:

- `SKYNET_E2E_CONTAINER_LOG_STREAM_ENABLED=1`
- `SKYNET_E2E_CONTAINER_LOG_SOURCES=openclaw-gateway,skynet-api`
- `SKYNET_E2E_CONTAINER_LOG_REQUIRE_STREAM=1`
- `SKYNET_E2E_CONTAINER_LOG_MAX_LINE_CHARS=1200`
- `SKYNET_E2E_CONTAINER_LOG_RING_LINES=300`
- `SKYNET_E2E_CONTAINER_LOG_TAIL_DEFAULT=100`
- `SKYNET_E2E_CONTAINER_LOG_TAIL_OVERRIDES=openclaw-gateway=200,skynet-api=100`
- `SKYNET_E2E_RUNTIME_TRACE_STALE_SECONDS=90`

Shared live E2E cleanup flags:

- `SKYNET_LIVE_E2E_CLEANUP_AFTER_RUN=1`
- `SKYNET_LIVE_E2E_CLEANUP_TARGETS=worker_launcher,worker_agent`
- `SKYNET_LIVE_E2E_CLEANUP_GRACE_SECONDS=5`

When cleanup is enabled, the shared runner tears down repo-rooted live E2E processes in `finally` after the run:

- tracked pytest subprocesses started by `openclaw-gateway/tests/e2e_live.py`
- `scripts/run_worker_agent.ps1` launcher trees
- repo-rooted `openclaw-agent/main.py` processes

Container stream SSH credential resolution order:

1. `SKYNET_E2E_CONTAINER_LOG_SSH_HOST`, `SKYNET_E2E_CONTAINER_LOG_SSH_USER`, `SKYNET_E2E_CONTAINER_LOG_SSH_KEY`
2. `OPENCLAW_TUNNEL_EC2_HOST`, `OPENCLAW_TUNNEL_EC2_USER`, `OPENCLAW_TUNNEL_SSH_KEY`
3. If stream is required and still unresolved, E2E fails early with `CONTAINER_LOG_STREAM_UNAVAILABLE`.

During `conversation` and `telegram_real`, live E2E now emits `container.log.stream.*`, `container.log.line`, and a final `container.log.bundle` event into the same trace file for single-timeline debugging.

When `SKYNET_E2E_LIVE=1`, gateway runtime handlers consume the normalized live-E2E policy directly for planner selection, coding stage selection, and router fallback. `/status` reports `live_e2e_active`, `live_e2e_flow`, and `live_e2e_effective_coding_stage_chain`, and shared preflight fails fast if the target gateway is not enforcing that policy.

### Telegram Coding Tracker

During coding sessions, SKYNET now maintains one live tracker message with:

- phase (`setup`, `milestone_extraction`, `milestone_review`, `milestone_execution`, `quality_gates`, `finalization`)
- node transitions (`work_*`, `critic_*`, `repair_*`, `gate_final`)
- coding stage (`codex`)
- OpenClaw orchestration session context (`session`, `runtime`, `queue`)
- strict gate progression (`infra_preflight`, `run_contract`, `lint`, `tests`, `smoke`)
- monotonic percent progress and stale-signal warning
- a live `Stop and Exit` control while the session is running

Tracker defaults (non-secret, from settings YAML):

- `SKYNET_TELEGRAM_TRACKER_ENABLED=true`
- `SKYNET_TELEGRAM_TRACKER_EDIT_INTERVAL_SECONDS=3`
- `SKYNET_TELEGRAM_TRACKER_STALE_WARN_SECONDS=90`
- `SKYNET_TELEGRAM_TRACKER_STUCK_EXIT_SECONDS=300`
- `SKYNET_TELEGRAM_TRACKER_WATCHDOG_POLL_SECONDS=5`
- `SKYNET_TELEGRAM_TRACKER_BAR_WIDTH=20`
- `SKYNET_TELEGRAM_TRACKER_VERBOSE_PIPELINE=true`

`/status` surfaces the same tracker state (progress bar, phase, stage/gate, transport, run-contract status).

Tracker troubleshooting:

- If phase is stuck at `milestone_extraction`, check provider health and `SKYNET_MILESTONE_EXTRACTION_*` timeouts.
- If the tracker has no new signal for `SKYNET_TELEGRAM_TRACKER_STUCK_EXIT_SECONDS`, the loop now finalizes as failed and exits instead of idling indefinitely.
- If phase shows repeated `GENERATION_FAILED: codex`, inspect worker Codex setup and write permissions.
- If phase is `quality_gates`, gate name identifies the blocker (`run_contract`, `lint`, `tests`, `smoke`).
- If transport is healthy but no stage progress is visible, inspect `telegram.tracker.*` logs in gateway output.
- If chat shows `coding preflight failed: no control-plane coding agents available`, you are running ACP checks in an SSH-first topology. Set `SKYNET_ORCHESTRATION_MODE=legacy` (or keep ACP and install CLIs in EC2 runtime).

### Control Loop v2 (Enabled By Default)

Current checked-in defaults combine the control loop with websocket-primary worker execution:

- `SKYNET_CONTROL_LOOP_ENABLED=true`
- `SKYNET_CONTROL_LOOP_DEFAULT_PROFILE=loop_v2`
- `SKYNET_ORCHESTRATION_MODE=legacy`
- `OPENCLAW_EXECUTION_MODE=agent_preferred`
- `SKYNET_CODING_TRANSPORT=websocket_primary`
- `OPENCLAW_SSH_FALLBACK_ENABLED=false`
- `SKYNET_WEBSOCKET_FALLBACK_TO_SSH=false`
- planner primary agent: `codex` (`SKYNET_PLANNER_PRIMARY_AGENT=codex`)
- coding fallback chain: `qwen,codex` (`SKYNET_CODING_FALLBACK_CHAIN=qwen,codex`)

The optional SSH-primary profile remains available, but it is no longer the default repo state.

Closed-loop config flags:

- `SKYNET_CONTROL_LOOP_ENABLED=1`
- `SKYNET_CONTROL_LOOP_FORCE_FOR_ALL=1`
- `SKYNET_CONTROL_LOOP_DEFAULT_PROFILE=loop_v2`
- `SKYNET_CONTROL_LOOP_REPAIR_RETRIES=1`
- `SKYNET_CONTROL_LOOP_CRITIC_BLOCK_THRESHOLD=high`
- `SKYNET_CONTROL_LOOP_REVIEW_CRITIC_ENABLED=1`
- `SKYNET_CONTROL_LOOP_ROUTER_FALLBACK_ENABLED=0` (planner/milestone fallback disabled by default)
- `SKYNET_CONTROL_LOOP_DIRECTOR_ENABLED=1`
- `SKYNET_CONTROL_LOOP_ARCHITECT_ENABLED=1`
- `SKYNET_CONTROL_LOOP_ARCH_BLOCKING=1`
- `SKYNET_CONTROL_LOOP_WORKER_POOL_ENABLED=1`
- `SKYNET_CONTROL_LOOP_LEARNING_ENABLED=1`

Closed-loop persistence tables:

- `task_graphs`
- `task_nodes`
- `critic_findings`
- `project_memory`
- `architecture_states`
- `task_strategy`
- `worker_registry`
- `node_worker_assignments`
- `learning_events`
- `prompt_policies`
- `task_orchestration_runs` (stage/session telemetry)

### ACP-First Orchestration (EC2 Agents, Worker SSH)

`acp_first` mode runs coding/planner/milestone generation on the control plane and keeps the worker as an SSH execution/file target.

Key non-secret flags:

- `SKYNET_ORCHESTRATION_MODE=acp_first`
- `SKYNET_OPENCLAW_RUNTIME=acp`
- `SKYNET_OPENCLAW_QUEUE_MODE=require_empty_queue`
- `SKYNET_OPENCLAW_RETRY_TRANSIENT=1`
- `SKYNET_OPENCLAW_SESSION_TIMEOUT_SECONDS=1800`
- `SKYNET_OPENCLAW_STAGE_CHAIN=codex,claude,cline`
- `SKYNET_OPENCLAW_AGENT_HOSTING=ec2_control`
- `SKYNET_OPENCLAW_TRACE_ENABLED=1`
- `OPENCLAW_CODEX_BIN`, `OPENCLAW_CLAUDE_BIN`, `OPENCLAW_CLINE_BIN`

Behavior:

- coding fallback chain remains deterministic (`codex -> claude -> cline`)
- strict gates remain blocking before milestone `done`
- strict run remains manifest-driven (`skynet_run.json`)
- stage/session telemetry is persisted in `task_orchestration_runs`

Runtime matrix:

- SSH-first (`OPENCLAW_EXECUTION_MODE=ssh_tunnel`):
  - agent CLIs are required on the worker host.
  - gateway dispatches coding/planner actions over SSH.
- ACP-first (`SKYNET_ORCHESTRATION_MODE=acp_first` with no SSH override):
  - agent CLIs are required in the EC2 control-plane runtime/container.
  - worker remains execution/file target for run/lint/tests/smoke.

For real Telegram-network E2E, set:

- `SKYNET_E2E_TELEGRAM_API_ID`
- `SKYNET_E2E_TELEGRAM_API_HASH`
- `SKYNET_E2E_TELEGRAM_SESSION`
- `SKYNET_E2E_TELEGRAM_BOT_USERNAME`

And run:

```powershell
pip install telethon
$env:SKYNET_ENV_FILE = ".env.local-e2e"
$env:SKYNET_LIVE_E2E_FLOW = "telegram_real"
python openclaw-gateway/tests/e2e_live.py
```

### SSH Reliability Controls

These env vars are enabled for SSH-primary hardening:

- `OPENCLAW_SSH_MAX_PARALLEL` (default `2`)
- `OPENCLAW_SSH_CIRCUIT_BREAKER_SECONDS` (default `60`)
- `OPENCLAW_SSH_CAPACITY_BACKOFF_SECONDS` (default `30`)
- `OPENCLAW_SSH_HEALTH_PROBE_TIMEOUT` (default `6`)
- `SKYNET_E2E_FAIL_ON_SKIP` (default `1`, prevents false-green live E2E)

### Canonical Tunnel Task Workflow (Windows laptop)

Use the built-in scripts under `scripts/`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_tunnel_task.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\check_tunnel_health.ps1
```

Recommended worker env before registration:

```powershell
$env:OPENCLAW_TUNNEL_SSH_KEY = "E:\MyProjects\skynet-key.pem"
$env:OPENCLAW_SSH_KEY_PATH = "E:\MyProjects\skynet-key.pem"
$env:OPENCLAW_TUNNEL_REMOTE_BIND_HOST = "0.0.0.0"
```

Canonical scheduled task name: `OpenClawReverseTunnel`.

Tunnel owner policy:

- Only `OpenClawReverseTunnel` pointing to `scripts/keep_tunnel_alive.ps1` is supported.
- Legacy launchers (for example `E:\MyProjects\openclaw-tunnel.ps1`) are treated as owner mismatch and must be disabled.
- `scripts/keep_tunnel_alive.ps1` resolves key precedence as:
  - `OPENCLAW_TUNNEL_SSH_KEY`
  - `OPENCLAW_SSH_KEY_PATH`
  - `E:\MyProjects\skynet-key.pem` (canonical default)

One-command repair:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\repair_tunnel_owner.ps1
```
