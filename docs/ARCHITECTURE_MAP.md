# SKYNET Architecture Map

Current-code-first map of runtime topology, boundaries, and ownership.

## Runtime Topology

- `skynet/`: control plane API, registry, queue authority, scheduler, stale-lock reaper.
- `openclaw-gateway/`: orchestration gateway (Telegram bot + HTTP dispatch + WS bridge + SSH fallback).
- `openclaw-agent/`: execution worker (security-gated action router + action executors + audit logging).

High-level flow:

1. User interacts with gateway (Telegram handlers).
2. Gateway dispatches to worker either:
   - directly via WebSocket (`openclaw-agent` connected), or
   - via SSH tunnel fallback (`openclaw-gateway/ssh_tunnel_executor.py`).
3. Control-plane scheduler in `skynet/` claims queued tasks and delegates to gateway `/action`.

## Entrypoints

| Component | Entrypoint | Purpose |
| --- | --- | --- |
| Control plane service | `skynet/main.py` | Starts FastAPI app via Uvicorn. |
| Control plane app | `skynet/api/main.py` | Lifespan init (registry, queue, scheduler, reaper), API mount. |
| Gateway runtime | `openclaw-gateway/main.py` | Starts DB, AI router, WS server, HTTP API, Telegram polling. |
| Gateway WS bridge | `openclaw-gateway/gateway.py` | Worker connection/auth + action request/response routing. |
| Gateway HTTP API | `openclaw-gateway/api.py` | `/status`, `/action`, emergency controls, profile memory endpoints. |
| Worker runtime | `openclaw-agent/main.py` | Starts outbound WS loop to gateway. |
| Worker WS client | `openclaw-agent/connection/websocket_client.py` | Receives action requests, sends responses, reconnect loop. |

## Data Stores

| Store | Path / Schema | Owner | Usage |
| --- | --- | --- | --- |
| Control-plane SQLite | `skynet/ledger/schema.py` | `skynet/` | `control_tasks`, file ownership, task events, workers, job locks. |
| Gateway SQLite | `openclaw-gateway/db/schema.py` | `openclaw-gateway/` | users, projects, tasks, provider usage, memory/profile tables. |
| Agent audit log | `openclaw-agent/logs/audit.jsonl` | `openclaw-agent/` | append-only action audit trail. |

## Ownership Boundaries

- `skynet/` owns orchestration state and task scheduling authority.
- `openclaw-gateway/` owns transport orchestration and user-facing bot flows.
- `openclaw-agent/` (or SSH fallback) owns execution of tool/actions.

Forbidden boundary violations:

- `skynet/` must not execute shell/tools/runtime logic.
- `skynet/` must not own agent memory/session/runtime provider execution behavior.
- Runtime behavior changes must be implemented in `openclaw-gateway/` or `openclaw-agent/`.

Enforcement hooks:

- `scripts/ci/check_control_plane_boundary.py`
- `scripts/ci/check_stale_paths.py`
- `scripts/ci/check_engineering_policy.py`
