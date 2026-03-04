# SKYNET Control Plane

Authoritative contract: `docs/SKYNET_OPENCLAW_CONTRACT.md`.
Implementation/debug/policy docs: `docs/INDEX.md`.

- OpenClaw executes tasks.
- SKYNET orchestrates OpenClaw gateways.
- SKYNET does not run agent runtime/tool execution logic.

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

## Sync Local `.env` To GitHub (No Commit)

Use this to push local `.env` values into repository Actions secrets:

```bash
python scripts/dev/sync_env_to_github.py --mode secrets
```

Safety behavior:
- Keys with reserved prefix `GITHUB_` are skipped automatically.
- Keys with empty values are skipped automatically.
- Invalid key names are skipped automatically.
- When repo secret quota is full, new secret names are skipped automatically.
- Only actual GitHub API write failures return non-zero.

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
make test
make smoke
python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD
```

Primary API tests:
- `tests/test_api_lifespan.py`
- `tests/test_api_provider_config.py`
- `tests/test_api_control_plane.py`

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
1. `gemini`
2. `groq`
3. `openrouter`
4. `deepseek`
5. `openai`
6. `claude`
7. `ollama`

Override via `AI_PROVIDER_PRIORITY` (comma-separated).

## Autonomous Project Startup

Default behavior now supports end-to-end autonomous project execution:

1. New projects are created under `SKYNET_PROJECT_BASE_DIR` (default `E:\MyProjects`).
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

Canonical scheduled task name: `OpenClawReverseTunnel`.

Tunnel owner policy:

- Only `OpenClawReverseTunnel` pointing to `scripts/keep_tunnel_alive.ps1` is supported.
- Legacy launchers (for example `E:\MyProjects\openclaw-tunnel.ps1`) are treated as owner mismatch and must be disabled.

One-command repair:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\repair_tunnel_owner.ps1
```
