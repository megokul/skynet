# SKYNET Implementation Guide

Module-level implementation reference for coding agents.

## Control Plane Implementation

Primary modules:

- `skynet/api/routes.py`: `/v1/*` control-plane routes.
- `skynet/api/schemas.py`: request/response models.
- `skynet/control_plane/scheduler.py`: claim -> run -> dispatch -> complete/release loop.
- `skynet/control_plane/reaper.py`: stale-lock monitor (`failed_timeout` or release).
- `skynet/ledger/task_queue.py`: authoritative task state machine and lock semantics.
- `skynet/control_plane/gateway_client.py`: gateway `/status` and `/action` HTTP client.

Control-plane task states:

- `queued`, `claimed`, `running`, `succeeded`, `failed`, `released`, `failed_timeout`
- Canonical transitions are enforced in `TaskQueueManager`.

Read models:

- `/v1/tasks`, `/v1/tasks/next`, `/v1/agents`, `/v1/events`, `/v1/files/ownership`

## Gateway Implementation

Primary modules:

- `openclaw-gateway/main.py`: bootstraps DB/router/server/bot.
- `openclaw-gateway/gateway.py`: WS auth + pending request map + send_action.
- `openclaw-gateway/api.py`: `/status`, `/action`, idempotency cache, SSH fallback routing.
- `openclaw-gateway/bot/`: Telegram handlers (`project.py`, `coding.py`, `chat.py`, `greeting.py`).
- `openclaw-gateway/ssh_tunnel_executor.py`: allowlisted remote execution path.

Dispatch behavior:

1. `/action` validates body and optional `(task_id, idempotency_key)`.
2. Returns cached idempotent result when available.
3. Uses WS agent path when connected.
4. Uses SSH fallback when configured or forced via `OPENCLAW_EXECUTION_MODE`.

## Conversation UX Contract

Telegram keyboard placement rules are strict and should be treated as a UI contract:

- Primary CTA appears in row 1 and is unique for the step.
- Destructive action (for example `Stop Session`) is isolated on the final row.
- Navigation row is last and ordered as `My Projects` then `Main Menu` when both appear.
- Recovery actions (for example `Retry Coding`) must be explicit buttons, not text-only hints.
- Failure summaries must only show CTAs that are valid for that state (no `Run Project` when all milestones failed).

## Agent Implementation

Primary modules:

- `openclaw-agent/connection/websocket_client.py`: WS session + message handling.
- `openclaw-agent/router/action_router.py`: rate limit, security validation, tier dispatch.
- `openclaw-agent/security/validator.py`: action allowlist + path jail + param sanitization.
- `openclaw-agent/executor/actions.py`: action implementations and registry.
- `openclaw-agent/audit/logger.py`: append-only JSONL audit entries.

Execution tiers are defined in `openclaw-agent/config.py`:

- `AUTO_ACTIONS`: no terminal approval.
- `CONFIRM_ACTIONS`: requires approval unless `confirmed=true`.
- unknown actions are blocked.

## Environment Precedence and Critical Flags

Control plane:

- `OPENCLAW_GATEWAY_URLS` (comma-separated) overrides `OPENCLAW_GATEWAY_URL`.
- `SKYNET_CONTROL_SCHEDULER_ENABLED`, `SKYNET_STALE_LOCK_REAPER_ENABLED`
- `SKYNET_CONTROL_TASK_LOCK_TIMEOUT`

Gateway:

- `OPENCLAW_EXECUTION_MODE` (`ssh`, `ssh_tunnel`, etc.) can force SSH mode.
- `OPENCLAW_SSH_*` controls fallback target, auth, path jail roots, and timeouts.
- `AI_PROVIDER_PRIORITY`, provider API keys, and model flags drive routing.
- `SKYNET_TRACE_MIRROR_LOG_DIR`, `SKYNET_LOG_ENABLE_SSH_MIRROR` affect trace sink behavior.

Agent:

- `SKYNET_GATEWAY_URL`, `SKYNET_AUTH_TOKEN`
- `SKYNET_ALLOWED_ROOTS` path jail roots
- `RATE_LIMIT_PER_MINUTE` from config module default/override

## Action Surfaces

Gateway SSH executor supported actions (see `openclaw-gateway/ssh_tunnel_executor.py`):

- File ops: `file_read`, `file_write`, `create_directory`, `list_directory`
- Git/GitHub: `git_status`, `git_init`, `git_add_all`, `git_commit`, `git_push`, `gh_create_repo`
- Build/test: `run_tests`, `lint_project`, `build_project`, `install_dependencies`
- Runtime: `run_coding_agent`, `check_coding_agents`, `configure_coding_agent`, `exec_command`
- Docker/desktop: `docker_build`, `docker_compose_up`, `open_in_vscode`, `close_app`
- Search: `web_search`

Agent executor registry (see `openclaw-agent/executor/actions.py`):

- AUTO: `git_status`, `web_search`, `run_tests`, `lint_project`, `start_dev_server`, `build_project`, `file_read`, `list_directory`, `ollama_chat`, `check_coding_agents`
- CONFIRM: `git_commit`, `install_dependencies`, `file_write`, `create_directory`, `delete_directory`, `git_init`, `git_add_all`, `git_push`, `gh_create_repo`, `open_in_vscode`, `run_coding_agent`, `exec_command`, `docker_build`, `docker_compose_up`, `close_app`, `zip_project`

## Safe-Edit Hotspots and High-Risk Files

Safe-edit hotspots:

- `skynet/api/routes.py` route behavior and response contracts.
- `skynet/ledger/task_queue.py` scheduling and transition logic.
- `openclaw-gateway/bot/handlers/*.py` Telegram behavior.
- `openclaw-gateway/api.py` dispatch/idempotency behavior.

High-risk files:

- `skynet/ledger/task_queue.py` (state machine regressions can break orchestration).
- `openclaw-gateway/ssh_tunnel_executor.py` (remote execution + path safety).
- `openclaw-agent/security/validator.py` (security invariants).
- `.github/workflows/deploy-ec2-skynet.yml` (deployment safety).

## Local Verification Commands

Control-plane focused:

```bash
python -m pytest tests/test_api_lifespan.py tests/test_api_provider_config.py tests/test_api_control_plane.py tests/test_job_locking.py tests/test_worker_registry.py -q
```

Gateway focused:

```bash
python -m pytest openclaw-gateway/tests -q
```

Agent focused:

```bash
python -m pytest openclaw-agent/tests -q
```

Policy and guardrails:

```bash
python scripts/ci/check_stale_paths.py
python scripts/ci/check_control_plane_boundary.py
python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD
```

Deterministic gateway conversation E2E (CI-safe, local bare git remote push):

```bash
python -m pytest openclaw-gateway/tests/test_conversation_e2e_repo_push.py -q
```

Manual live conversation E2E (real planner + codegen + GitHub push over SSH):

```bash
SKYNET_E2E_LIVE=1 python -m pytest openclaw-gateway/tests/test_e2e_conversation_live.py -m live -q
```

Live E2E prerequisites checklist:

- `OPENCLAW_EXECUTION_MODE=ssh`
- `OPENCLAW_SSH_HOST`, `OPENCLAW_SSH_USER` (and key/password settings as required)
- worker has `gh` authenticated (`gh auth status`)
- at least one usable planner provider path in environment
- `OPENCLAW_PROJECT_BASE_DIR` points to writable worker directory
