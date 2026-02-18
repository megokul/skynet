# SKYNET/OpenClaw Contract

Last Updated: 2026-02-18
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

## Control flow
User -> OpenClaw Gateway -> SKYNET (optional orchestration) -> selected OpenClaw Gateway -> OpenClaw runtime -> OpenClaw worker

## Implementation rule for coding agents
Any code in `skynet/` must remain orchestration and infrastructure-only.
Any execution/runtime/tool logic belongs in `openclaw-gateway/` or `openclaw-agent/`.
