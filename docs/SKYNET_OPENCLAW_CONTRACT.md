# SKYNET/OpenClaw Contract

Last Updated: 2026-02-20
Status: Authoritative

## One-line contract
OpenClaw executes tasks. SKYNET orchestrates OpenClaw instances.

## Layered model
- Layer 5: User interfaces (Telegram/Web/API)
- Layer 4: SKYNET control plane (distributed orchestration)
- Layer 3: OpenClaw gateway (agent runtime)
- Layer 2: OpenClaw workers (tool execution and compute)
- Layer 1: Physical infrastructure (EC2/laptop/servers)

## OpenClaw responsibilities (execution runtime)
- Agent runtime management: lifecycle, spawning, sub-agents, sessions, memory, tool access
- Execution engine: shell/file/tool/sandbox/browser/script execution
- Tool system: registration, permissioning, isolation
- Model provider integration for agent execution
- Worker execution environments and process/filesystem execution
- Session and memory systems
- Communication channels (Telegram/Web/API)

## SKYNET responsibilities (control plane only)
- Gateway registry: registration, identity, metadata, heartbeat status
- Worker registry: worker health and capacity metadata only
- Distributed routing: select gateway and forward task
- Infrastructure state: topology and node relationships
- Health monitoring: gateway/worker status and failure detection
- Failover and redundancy: redirect to healthy gateways
- Global scheduling at infrastructure level only
- Policy layer for infrastructure access/assignment policy

## Explicitly forbidden in SKYNET
- Agent runtime implementation
- Tool execution
- Shell/file/sandbox/browser/script execution
- Worker workload execution
- Replacing OpenClaw session or memory systems
- Direct model provider calls for agent execution

## Interface contract
OpenClaw gateway API exposed to SKYNET:
- execute_task()
- get_gateway_status()
- get_worker_status()
- list_sessions()

SKYNET control-plane API:
- register_gateway()
- register_worker()
- route_task()
- get_system_state()

## Dispatch Idempotency Contract
- Control plane dispatches `claim_token` as `idempotency_key` and includes `task_id`.
- Gateway caches `(task_id, idempotency_key) -> execution_result` in SQLite (`action_idempotency`).
- Worker receives the same key and short-circuits duplicate requests from local idempotency cache.

## Task State Machine (Authoritative)
States:
- `queued`
- `claimed`
- `running`
- `succeeded`
- `failed`
- `released`
- `failed_timeout`

Legal transitions:
- `queued -> claimed`
- `released -> claimed`
- `claimed -> running|released|failed|failed_timeout`
- `running -> succeeded|failed|released|failed_timeout`

Illegal transitions are rejected (example: complete without running, release after success).

## Stale-Lock Reaper
- Background reaper scans `claimed`/`running` tasks older than TTL.
- Reaper verifies worker and gateway health.
- Reaper either:
  - releases task back to `released`, or
  - marks task as `failed_timeout`.

## Read Models
Additional control-plane read-model endpoints:
- `GET /v1/tasks/next?agent_id=...` (dry-run eligibility, no lock)
- `GET /v1/agents` (who is working on what)
- `GET /v1/events` (task execution/event stream)

## Control flow
User -> OpenClaw Gateway -> SKYNET (optional orchestration) -> selected OpenClaw Gateway -> OpenClaw runtime -> OpenClaw worker

## Implementation rule for coding agents
Any code in `skynet/` must remain orchestration and infrastructure-only.
Any execution/runtime/tool logic belongs in `openclaw-gateway/` or `openclaw-agent/`.
