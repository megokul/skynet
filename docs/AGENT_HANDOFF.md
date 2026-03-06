# Agent Handoff

Last updated (UTC): 2026-03-06 20:05

## Current Goal

WebSocket-primary worker execution with SSH fallback, while preserving strict quality gates and deterministic run behavior.

### 2026-03-06 WebSocket-First Execution Model Rollout

- Gateway transport layer in `openclaw-gateway/gateway.py` now supports:
  - websocket-primary selection based on worker heartbeat freshness
  - explicit `gateway.transport.select` and `gateway.transport.fallback` trace events
  - `action_accepted` handling for in-flight websocket actions
  - replay path for accepted mutating actions
  - pre-accept SSH fallback on websocket send failure
  - websocket log-mirror ack tracking (`log_write_ack`)
- Gateway `/status` now exposes websocket health and mirror diagnostics in `openclaw-gateway/api.py`:
  - `primary_transport_mode`
  - `worker_id`
  - `agent_last_hello_at`
  - `agent_last_heartbeat_at`
  - `websocket_health_ok`
  - `websocket_error_category`
  - `websocket_failure_streak`
  - `fallback_ready`
  - `fallback_last_reason`
  - websocket log mirror send/ack/error fields
  - dynamic `primary_transport_mode` (`websocket_primary`, `ssh_fallback`, `unavailable`)
- Logging bootstrap now binds websocket mirror handlers to the runtime event loop:
  - `openclaw-gateway/logging_setup.py`
  - `openclaw-gateway/main.py`
- Worker websocket client now supports:
  - rich `agent_hello`
  - periodic `agent_heartbeat`
  - `action_accepted`
  - `log_write_ack`
  - concurrent action handling so long-running coding actions do not block probes/cancel requests
- Worker router now supports websocket replay identity via:
  - `task_id:idempotency_key`
  - fallback `transport_id:idempotency_key`
  - in-flight replay wait path
- Worker executor now exposes websocket-compatible runtime controls:
  - `trace_runtime_probe`
  - `cancel_runtime_session`
- Worker lifecycle scripts added:
  - `scripts/install_worker_agent.ps1`
  - `scripts/run_worker_agent.ps1`
  - `scripts/register_worker_agent_task.ps1`
  - `scripts/check_worker_agent_health.ps1`
  - `scripts/repair_worker_agent.ps1`
- Non-secret defaults/docs updated toward websocket-primary:
  - `openclaw-gateway/settings/settings.yaml`
  - `openclaw-gateway/settings/settings.example.yaml`
  - `.env.ec2-gateway.example`
  - `.env.local-e2e.example`
  - `.env.worker-agent.example`
  - `README.md`
  - `docs/IMPLEMENTATION_GUIDE.md`
  - `docs/DEBUG_PLAYBOOK.md`
  - `docs/ARCHITECTURE_MAP.md`
- Live Telegram E2E now proves websocket-primary selection from runtime trace:
  - `SKYNET_E2E_REQUIRE_WEBSOCKET_PRIMARY=1`
  - `SKYNET_E2E_ALLOW_SSH_FALLBACK=1`
  - runtime trace summary checks for `gateway.transport.select transport=websocket_primary`
- Telegram tracker/runtime trace surfaces now use dynamic transport/runtime labels instead of hardcoded SSH labels in websocket-primary runs.
- Mixed gateway/agent pytest runs no longer collide on the bare `config.py` module:
  - gateway code now imports `gateway_config.py`
  - worker code now imports `agent_config.py`
- Worker bootstrap fixes added after live bring-up:
  - `.env.worker-agent` parsing no longer aborts on comment lines
  - launcher writes `logs\worker-agent.bootstrap.log`
  - tunnel probe helper no longer collides with PowerShell's built-in `$Host`
- Live E2E preflight fix added after websocket-primary rollout:
  - worker `exec_command` now supports structured `argv`
  - coding preflight write-probe no longer sends a quoted `python -c "..."` shell string
  - this removes the false `CODEX_WRITE_BLOCKED: Parameter 'command' contains disallowed shell metacharacters` failure in websocket-primary runs
- Current workstation note:
  - `register_worker_agent_task.ps1` still hits local `Access is denied` on this laptop
  - detached user-session launch via `scripts/run_worker_agent.ps1` is the current operational workaround

### 2026-03-06 Max-Forensic Runtime Trace + Telegram Tracker Update

- Expanded runtime trace for SSH-first coding so stuck stages are diagnosable from `skynet.trace.log` alone:
  - added forensic trace envelope fields in `openclaw-gateway/runtime_trace.py`, `openclaw-gateway/db/schema.py`, and `openclaw-gateway/db/store.py`
    - `event_id`
    - `root_trace_id`
    - `session_key`
    - `remote_pid`
    - `artifact_count`
  - moved runtime redaction to regex-based forensic sanitization while preserving readable command/path/process diagnostics.
- Hardened SSH execution tracing in `openclaw-gateway/ssh_tunnel_executor.py`:
  - active session registry keyed by `session_key`
  - prompt-file lifecycle events
  - command launch/stdout/stderr/exit wait events
  - `trace_runtime_probe` and `cancel_runtime_session` actions
  - remote artifact/process snapshots for long-running `run_coding_agent` calls.
- Wired coding heartbeat probes and stop-cleanup tracing in `openclaw-gateway/bot/handlers/coding.py`:
  - `coding.stage.remote_snapshot`
  - `coding.stage.process_tree`
  - `coding.stage.prompt_file_state`
  - `coding.stage.artifact_detected`
  - `coding.stop.requested`
  - `coding.stop.remote_cancel`
  - `coding.stop.orphan_process.detected`
  - `/trace deep` now includes latest forensic snapshot summary.
- Fixed Telegram tracker/status correctness in `openclaw-gateway/bot/handlers/coding.py`:
  - SSH sessions now default to `transport=ssh_first` and `runtime=ssh`
  - meaningful phase/stage/gate changes bypass edit throttling immediately
  - tracker message stays rate-limited only for non-significant heartbeat churn.
- Normalized Telegram chat text in `openclaw-gateway/bot/handlers/coding.py` and `openclaw-gateway/bot/handlers/project.py`:
  - removed mojibake/corrupted symbols from milestone, retry, stop, and plan-generation messages.
- Deployment alignment fix:
  - updated `.github/workflows/deploy-ec2-skynet.yml` so `.env.ci` no longer forces legacy trace payload mode.
  - deploy now exports forensic trace defaults (`DETAIL_PROFILE=max_forensic`, `PAYLOAD_MODE=forensic_redacted`, remote snapshot/session-registry flags).

### 2026-03-06 Deployment Repair: Runtime Trace Schema Migration Order

- GitHub Actions run `22769176004` failed in `Deploy on self-hosted runner -> Verify deployment`.
- Root cause:
  - `openclaw-gateway` startup ran `init_db()` against an existing SQLite file whose `runtime_trace_events` table predated the new forensic columns.
  - `SCHEMA_SQL` attempted to create `idx_runtime_trace_session_created` before the migration step added `session_key`, causing:
    - `sqlite3.OperationalError: no such column: session_key`
- Fix in `openclaw-gateway/db/schema.py`:
  - removed runtime-trace indexes from bootstrap `SCHEMA_SQL`
  - re-created them only in the post-migration index pass, after `ALTER TABLE ... ADD COLUMN session_key/root_trace_id/event_id/remote_pid/artifact_count`
  - added missing post-migration indexes:
    - `idx_runtime_trace_session_created`
    - `idx_runtime_trace_project_graph_created`
- Regression coverage:
  - `openclaw-gateway/tests/test_runtime_trace_forensic.py::test_init_db_upgrades_legacy_runtime_trace_schema`
  - creates a legacy `runtime_trace_events` table and verifies `init_db()` upgrades columns and indexes safely.

### 2026-03-06 Trace-First Live E2E + Container Stream Update

- Added trace-first live diagnostics in `openclaw-gateway/tests/test_e2e_telegram_real_live.py`:
  - always-on EC2 container streaming events (`container.log.stream.start|ok|error|stop`, `container.log.line`)
  - per-container ring-buffer bundles on terminal failures (`container.log.bundle`)
  - runtime trace snapshot metadata (`mtime_iso`, `age_s`, `line_count`, `digest`)
  - stale runtime trace hard-fail classification (`TRACE_STALE`)
  - tracker-edit/runtime-trace/container activity checks in coding poll loop
  - `/trace deep` capture attempt before timeout-class failure.
- Added container-log redaction hardening:
  - bearer/auth/token-like field masking
  - explicit Telegram bot token URL masking (`bot[REDACTED]`).
- Added runtime trace/config propagation updates:
  - `openclaw-gateway/config.py`
  - `.github/workflows/deploy-ec2-skynet.yml`
  - `.env.example`, `.env.local-e2e.example`, `.env.ec2-gateway.example`
  - `openclaw-gateway/settings/settings.yaml`, `openclaw-gateway/settings/settings.example.yaml`
  - `README.md`.
- Added regression coverage:
  - `openclaw-gateway/tests/test_live_e2e_trace_logging.py` now covers container stream redaction + stale trace diagnostics behavior.
- Fixed control-plane API compatibility in `skynet/ledger/task_queue.py`:
  - `claim_next_ready_task(...)` now accepts optional `lock_timeout_seconds` to match scheduler caller.
- Deployment status from live debug:
  - GitHub Actions runs `22762262248` and `22762380178` failed policy gate (`Check engineering policy`) because `docs/AGENT_HANDOFF.md` was not included.
  - As a result, EC2 stayed on previous containers and continued emitting:
    - `TypeError: TaskQueueManager.claim_next_ready_task() got an unexpected keyword argument 'lock_timeout_seconds'`
  - Current objective is to pass policy gate and redeploy so runtime picks up the compatibility fix.

### 2026-03-06 Runtime Trace Source Selection Hardening

- Live E2E exposed a false-stale failure mode: runtime snapshot resolver preferred a stale mirror file (`E:\SKYNET-SANDBOX\logs\skynet.trace.log`) even when a newer repo-local runtime trace existed.
- Updated `openclaw-gateway/tests/test_e2e_telegram_real_live.py`:
  - runtime trace resolution now considers:
    - `SKYNET_E2E_RUNTIME_TRACE_FILE` (explicit override, hard priority)
    - `SKYNET_RUNTIME_TRACE_LIVE_FILE`
    - mirror/file fallbacks
  - for non-explicit paths, resolver now selects the freshest existing trace file by `mtime` to avoid stale-pin behavior during live runs.

### 2026-03-06 TRACE_STALE False-Positive Fix in Coding Poll Loop

- Root cause:
  - During active coding polls, stale detection checked `runtime_progress.stale_seconds()` before refreshing with a fresh runtime snapshot.
  - This produced false `TRACE_STALE` failures even when the same iteration snapshot showed new lines and recent `mtime`.
- Fix:
  - In `openclaw-gateway/tests/test_e2e_telegram_real_live.py`, stale branch now:
    - captures a fresh snapshot first,
    - re-observes progress,
    - recomputes staleness,
    - emits `coding.poll.recovered` and continues when freshness is restored.
  - Terminal stale failure now only triggers when staleness remains above threshold after refresh.

### 2026-03-06 SSH Mirror Log Sink Reliability Hardening

- New live E2E evidence:
  - runtime trace file stalled while gateway container logs showed repeated `Log sink flush failed` and Paramiko `Error reading SSH protocol banner`.
  - This indicates mirror transport instability under SSH load, not trace schema/collector gaps.
- Applied transport hardening in `openclaw-gateway/logging_setup.py` (`_SSHMirrorFileHandler`):
  - increased minimum connect timeout floor from `2s` to `8s` for mirror handshakes.
  - switched to explicit auth mode (`look_for_keys=False`, `allow_agent=False`) when key/password is provided.
  - enabled SSH transport keepalive (`15s`) after successful connect.
  - added exponential reconnect backoff after connect failures (capped growth) to reduce handshake storms.
  - downgraded backoff-cycle flush failures to concise deferred messages (avoid traceback flood).

### 2026-03-06 Codex Prompt Transport + Gate Heartbeat Update

- Root-cause from live Telegram E2E (`livee2e1772786667`):
  - Windows SSH Codex prompt delivery was brittle for multiline payloads because prompt text was passed via argv.
  - Gate auto-fix (`_run_quality_fix_pass`) could run silently long enough for live E2E polling to timeout even while work was still progressing.
- Applied fixes:
  - `openclaw-gateway/ssh_tunnel_executor.py`
    - Added stdin prompt delivery path for Windows (`codex exec -`) via `_run_windows_command_with_prompt_file(..., prompt_via_stdin=True)`.
    - Added `coding.prompt.transport` runtime trace event with prompt length/newline metrics and delivery mode (`stdin|argv`).
  - `openclaw-gateway/bot/handlers/coding.py`
    - `_run_quality_fix_pass` now supports heartbeat-wrapped `run_coding_agent` execution.
    - `_run_strict_quality_gates` now passes app/chat/user context to quality-fix path and emits periodic `run_contract` running updates during auto-fix.
    - Updated both control-loop and legacy strict-gate call sites to provide heartbeat context.
  - `openclaw-gateway/tests/test_ssh_executor_resilience.py`
    - Added regression test validating stdin prompt transport script generation on Windows prompt-file runner.
- CI note:
  - Policy gate run `22756380094` initially failed because `docs/AGENT_HANDOFF.md` was not included with code changes.
  - This handoff update resolves that policy requirement for the next push.

### 2026-03-06 Strict-Gate Long-Run Heartbeat Coverage Update

- Live E2E observation after deploy `22756451474`:
  - quality-fix heartbeat messages were visible, but strict-gate operations (`lint/tests/smoke`) still ran without chat heartbeat emissions.
  - Telegram real E2E timed out waiting for new bot messages while gates were still executing.
- Fix in `openclaw-gateway/bot/handlers/coding.py`:
  - Added `_run_gate_action(...)` helper inside `_run_strict_quality_gates(...)`.
  - Long-running gate actions now use `_send_action_with_heartbeat(...)` when app/chat context is available.
  - Covered actions:
    - `lint_project`
    - `run_tests`
    - `exec_command` (smoke)
  - Gate heartbeat updates now emit periodic `running` events with elapsed seconds and gate command context.

### 2026-03-06 Control-Loop Timeout Crash Hardening Update

- Root-cause from live Telegram E2E (`livee2e1772782693`):
  - `work_2` stage timed out after 900s (`WAIT_TIMEOUT`) and bubbled up as an uncaught exception.
  - The coding loop emitted a generic "unexpected error" and left the loop graph in an `active` state with a `running` work node.
- Applied hardening in `openclaw-gateway/bot/handlers/coding.py`:
  - `WAIT_TIMEOUT` during stage execution is now treated as a stage failure path (non-crashing) instead of an unconditional re-raise.
  - Stage heartbeat tracker runtime metadata now correctly reports SSH mode for SSH-stage executions.
  - Added a protective catch around `controller.run(...)` to convert unexpected controller exceptions into a structured failed graph result.
- Applied hardening in `openclaw-gateway/orchestration/loop_controller.py`:
  - Added executor exception guards for `work`, `critic`, and `gate` nodes.
  - On uncaught executor exception, node and graph are now atomically marked failed with persisted failure type and event telemetry.
- Added regression coverage:
  - `openclaw-gateway/tests/test_control_loop_integration.py::test_closed_loop_executor_exception_fails_graph_without_crash`

### 2026-03-06 Runtime Trace Module Deployment Fix

- CI deploy failure root cause on run `22754926134`:
  - `ModuleNotFoundError: No module named 'runtime_trace'` during `openclaw-gateway` startup.
  - The code imported `runtime_trace`, but module/supporting DB wiring were not included in the pushed commit.
- Fix set staged for deployment:
  - Add `openclaw-gateway/runtime_trace.py`.
  - Add DB persistence support for runtime trace events in `db/schema.py` and `db/store.py`.
  - Include gateway/e2e runtime trace integration and associated tests/docs.

### 2026-03-05 Live E2E Runtime Trace Visibility Update

- Live E2E trace defaults now write to repo logs directory:
  - `E:\MyProjects\skynet\logs\`
- Updated live trace loggers:
  - `openclaw-gateway/tests/e2e_live.py`
  - `openclaw-gateway/tests/test_e2e_conversation_live.py`
  - `openclaw-gateway/tests/test_e2e_telegram_real_live.py`
- Added runtime trace snapshots throughout Telegram real E2E cycle:
  - checkpoints at plan/coding transitions, milestone clicks, periodic coding polls, run output, and terminal failures.
  - runtime source defaults to `logs/skynet.trace.log` (override via `SKYNET_E2E_RUNTIME_TRACE_FILE`).
- Added fast-fail detection for coding session failure summary in Telegram real E2E:
  - no more silent polling until timeout after `session failed`.
- Added new tests:
  - `openclaw-gateway/tests/test_live_e2e_trace_logging.py`
- Updated operator docs:
  - `README.md` live trace location and live monitoring commands.
  - `docs/DEBUG_PLAYBOOK.md` now requires runtime trace tail evidence in mitigation loop.

### 2026-03-05 SSH-First Hierarchical Closed Loop v1.2 Update

- Added `loop_v2` profile defaults and force-all behavior:
  - `SKYNET_CONTROL_LOOP_DEFAULT_PROFILE=loop_v2`
  - `SKYNET_CONTROL_LOOP_FORCE_FOR_ALL=1`
- Added v1.2 config flags in `openclaw-gateway/config.py` and settings templates:
  - director/architect toggles
  - blocking architecture contract controls
  - worker-pool selection controls
  - learning policy controls
  - dedicated director/architect/planner timeouts
- Added additive DB schema for hierarchical orchestration state:
  - `architecture_states`
  - `task_strategy`
  - `worker_registry`
  - `node_worker_assignments`
  - `learning_events`
  - `prompt_policies`
  - plus `task_nodes.worker_id`, `task_nodes.tools_required_json`, `task_nodes.risk_level`
- Added store APIs for architecture versioning, worker assignment, learning events, and prompt policies in `openclaw-gateway/db/store.py`.
- Added orchestration modules:
  - `openclaw-gateway/orchestration/director.py`
  - `openclaw-gateway/orchestration/architect.py`
  - `openclaw-gateway/orchestration/worker_pool.py`
  - `openclaw-gateway/orchestration/learning.py`
- Wired coding loop path to treat `loop_v2` as active control-loop mode while preserving SSH-first execution and codex-only stage chain.
- Added loop-v2 telemetry fields to tracker/status surfaces:
  - architecture version
  - worker assignment id
  - learning policy activation trace events.

### 2026-03-05 SSH-First Closed Loop v1 Update

- Added closed-loop config surface in `openclaw-gateway/config.py`:
  - `SKYNET_CONTROL_LOOP_*` flags for enable/force/profile/retry/critic/memory controls.
  - coding fallback default now `codex` and planner default `codex`.
- Added DB schema for persistent loop state in `openclaw-gateway/db/schema.py`:
  - `projects.control_loop_profile`
  - `task_graphs`
  - `task_nodes`
  - `critic_findings`
  - `project_memory`
- Added store APIs in `openclaw-gateway/db/store.py` for graph nodes, critic findings, and project memory tiers.
- Added orchestration modules:
  - `openclaw-gateway/orchestration/graph.py`
  - `openclaw-gateway/orchestration/critic.py`
  - `openclaw-gateway/orchestration/memory.py`
  - `openclaw-gateway/orchestration/loop_controller.py`
- Wired coding loop integration in `openclaw-gateway/bot/handlers/coding.py`:
  - `loop_v1` path executes a persisted node graph (`work -> critic -> repair -> gate`).
  - manual milestone approval remains for `work` nodes.
  - strict gates remain blocking before node completion.
  - codex-only generation stage enforced for loop mode.
  - tracker/status now include graph/node metadata.
- Updated project creation defaults in `openclaw-gateway/bot/handlers/project.py`:
  - sets `control_loop_profile` for new projects (`loop_v1` when enabled/forced).
- Updated templates/settings:
  - `.env.example`
  - `.env.local-e2e.example`
  - `.env.ec2-gateway.example`
  - `openclaw-gateway/settings/settings.yaml`
  - `openclaw-gateway/settings/settings.example.yaml`
- Added closed-loop tests:
  - `test_control_loop_graph.py`
  - `test_control_loop_critic.py`
  - `test_control_loop_memory.py`
  - `test_control_loop_integration.py`

### 2026-03-05 Reverse Tunnel Key/Bind + Codex Trust Fix

- Updated reverse tunnel scripts to use canonical worker key precedence:
  - `OPENCLAW_TUNNEL_SSH_KEY`
  - `OPENCLAW_SSH_KEY_PATH`
  - fallback `E:\MyProjects\skynet-key.pem`
- Removed invalid fallback to `protech-bot-key.pem` from:
  - `scripts/keep_tunnel_alive.ps1`
- Added explicit reverse-bind host support and set canonical bind shape:
  - `-R 0.0.0.0:2222:localhost:22`
- Hardened task registration to pass explicit tunnel args (key/host/bind/ports):
  - `scripts/register_tunnel_task.ps1`
- Hardened tunnel health diagnostics:
  - effective key path output
  - bind expectation output
  - improved ssh process matching for `-R` argument forms
  - `scripts/check_tunnel_health.ps1`
- Updated env/docs for canonical tunnel key:
  - `.env.ec2-gateway.example`
  - `.env.example`
  - `.env.local-e2e.example`
  - `README.md`
- Added codex SSH invocation trust bypass (`--skip-git-repo-check`) in:
  - `openclaw-gateway/ssh_tunnel_executor.py`
  - `openclaw-agent/executor/actions.py`
- Live Telegram E2E currently progresses through full conversation and SSH preflight, but milestone generation still fails on deployed runtime until latest gateway image is redeployed.

### 2026-03-05 SSH/ACP Precedence Guard Update

- Added orchestration precedence guard in `openclaw-gateway/config.py`:
  - new flag: `SKYNET_ORCHESTRATION_ALLOW_ACP_WITH_SSH` (default `0`)
  - new helpers:
    - `is_ssh_execution_mode()`
    - `effective_orchestration_mode()`
- Behavior lock:
  - when `OPENCLAW_EXECUTION_MODE` is `ssh|ssh_tunnel|tunnel|ssh-only`, effective orchestration mode is forced to `legacy` unless override flag is set.
- Switched ACP mode checks to effective mode in:
  - `openclaw-gateway/bot/handlers/coding.py`
  - `openclaw-gateway/bot/handlers/project.py`
  - `openclaw-gateway/gateway.py`
- Live Telegram E2E now fails fast on terminal preflight failures instead of timing out in polling loop:
  - `openclaw-gateway/tests/test_e2e_telegram_real_live.py`
- Deployment/env alignment updates:
  - `.github/workflows/deploy-ec2-skynet.yml`
  - `.env.ec2-gateway.example`
  - `.env.example`
  - `openclaw-gateway/settings/settings.example.yaml`

### 2026-03-05 ACP-First OpenClaw Orchestration Update

- Added control-plane orchestration config in `openclaw-gateway/config.py`:
  - `SKYNET_ORCHESTRATION_MODE`
  - `SKYNET_OPENCLAW_RUNTIME`
  - `SKYNET_OPENCLAW_QUEUE_MODE`
  - `SKYNET_OPENCLAW_RETRY_TRANSIENT`
  - `SKYNET_OPENCLAW_SESSION_TIMEOUT_SECONDS`
  - `SKYNET_OPENCLAW_STAGE_CHAIN`
  - `SKYNET_OPENCLAW_AGENT_HOSTING`
  - `SKYNET_OPENCLAW_TRACE_ENABLED`
  - `SKYNET_OPENCLAW_CLI_BIN`
  - `OPENCLAW_CODEX_BIN`, `OPENCLAW_CLAUDE_BIN`, `OPENCLAW_CLINE_BIN`
- Added orchestration adapter module:
  - `openclaw-gateway/orchestration/openclaw_runner.py`
  - session lifecycle methods: `start_session`, `run_prompt`, `wait`, `cancel`, `collect_trace`
- Added orchestration policy file:
  - `openclaw-gateway/settings/openclaw_profiles.yaml`
- Added orchestration telemetry persistence:
  - new table `task_orchestration_runs` in `openclaw-gateway/db/schema.py`
  - store methods in `openclaw-gateway/db/store.py`:
    - `create_task_orchestration_run`
    - `list_task_orchestration_runs`
- Wired coding stage chain to orchestration adapter in `openclaw-gateway/bot/handlers/coding.py` for `acp_first`:
  - stage execution now supports control-plane generation with worker `file_write` application.
  - stage start/success/fail are persisted to `task_orchestration_runs`.
  - preflight now supports control-plane stage availability checks.
- Wired planner and milestone extraction to orchestration-first path:
  - `openclaw-gateway/bot/handlers/project.py`
  - `openclaw-gateway/bot/handlers/coding.py`
- Added local coding-action routing for ACP-first mode in `openclaw-gateway/gateway.py`:
  - coding actions can resolve via local orchestration adapter when configured.
- Extended tracker visibility:
  - tracker pipeline now includes `session`, `runtime`, and `queue`.
- Updated deploy and settings templates for orchestration flags:
  - `.github/workflows/deploy-ec2-skynet.yml`
  - `openclaw-gateway/settings/settings.yaml`
  - `openclaw-gateway/settings/settings.example.yaml`
  - `.env.example`
  - `.env.local-e2e.example`

### 2026-03-04 Telegram Tracker + Pipeline Visibility Update

- Added Telegram coding tracker config flags in `openclaw-gateway/config.py`:
  - `SKYNET_TELEGRAM_TRACKER_ENABLED`
  - `SKYNET_TELEGRAM_TRACKER_EDIT_INTERVAL_SECONDS`
  - `SKYNET_TELEGRAM_TRACKER_STALE_WARN_SECONDS`
  - `SKYNET_TELEGRAM_TRACKER_BAR_WIDTH`
  - `SKYNET_TELEGRAM_TRACKER_VERBOSE_PIPELINE`
- Added matching non-secret defaults in:
  - `openclaw-gateway/settings/settings.yaml`
  - `openclaw-gateway/settings/settings.example.yaml`
  - optional env overrides in `.env.example` and `.env.local-e2e.example`.
- Completed coding-loop tracker wiring in `openclaw-gateway/bot/handlers/coding.py`:
  - setup, extraction, approval wait, execution, stage-chain switching, strict gates, and finalization.
  - stage hook events now surface stage transitions in tracker state.
  - strict gate hook events now surface gate-level progress and run-contract status.
- `/status` now reads tracker state and shows progress/phase/stage/gate/transport/run-contract details.
- Real Telegram E2E test upgraded to assert tracker visibility and observed tracker edits:
  - `openclaw-gateway/tests/test_e2e_telegram_real_live.py`
  - `openclaw-gateway/tests/e2e_live.py` now logs tracker summary counts from live output.

### 2026-03-04 Settings/Secrets Trim Update

- Added non-secret runtime defaults file:
  - `openclaw-gateway/settings/settings.yaml`
- Added clean example:
  - `openclaw-gateway/settings/settings.example.yaml`
- Refactored `openclaw-gateway/config.py`:
  - loads non-secret defaults from YAML
  - secrets remain env-only
  - added typed accessors `get_str/get_int/get_bool`
  - removed environment mutation shim
- Routed direct runtime env reads through config accessors:
  - `openclaw-gateway/gateway.py`
  - `openclaw-gateway/api.py`
  - `openclaw-gateway/ssh_tunnel_executor.py`
- Trimmed env templates to secret-focused shape:
  - `.env.example`
  - `.env.local-e2e.example`
- Rebuilt GitHub sync utility (`scripts/dev/sync_env_to_github.py`):
  - secret-like keys only by default in secrets mode
  - optional stale secret pruning (`--prune-stale`)
  - workflow-aware keep-set protection while pruning

### 2026-03-04 Pre-Push Secret Sync Reliability Update

- Hardened `scripts/dev/sync_env_to_github.py` so pre-push sync no longer fails on predictable GitHub constraints.
- Added automatic skip rules for env sync:
  - reserved `GITHUB_*` key names
  - empty values
  - invalid secret/variable names
- Added repository secret-quota guard:
  - when GitHub repo secret count reaches configured limit (default `100`), new secret names are skipped instead of failing pre-push.
- Updated sync summary output to include `skipped=` counts.
- Updated `README.md` sync section to document skip behavior and non-zero failure semantics.

### 2026-03-04 EC2->Worker Control Path Stabilization Update

- Refactored `openclaw-gateway/tests/e2e_live.py` preflight to be flow-aware:
  - `telegram_real` now validates only `SKYNET_E2E_TELEGRAM_*` vars.
  - `conversation`/`direct` continue validating `OPENCLAW_SSH_*`.
  - `host.docker.internal` context guard now applies only to SSH-dependent flows.
- Added explicit missing-env trace event in real Telegram test:
  - `telegram_real.env.missing` in `test_e2e_telegram_real_live.py`.
- Hardened tunnel ownership and diagnostics scripts:
  - `register_tunnel_task.ps1` now enforces canonical script ownership (`scripts/keep_tunnel_alive.ps1`) and starts the task by default (`-StartNow`).
  - `check_tunnel_health.ps1` now reports structured health categories:
    - `healthy`, `auth`, `port_conflict`, `owner_mismatch`, `ec2_unreachable`, `remote_bind_missing`.
  - Added `scripts/repair_tunnel_owner.ps1` one-command remediation wrapper.
- Updated docs/templates with explicit runtime matrix and tunnel-owner policy:
  - `.env.example`, `.env.local-e2e.example`, and `README.md`.

### 2026-03-04 Live-E2E Context Guard + Telegram Trace Update

- Added explicit runtime-context guard in `openclaw-gateway/tests/e2e_live.py`:
  - fails fast when `OPENCLAW_SSH_HOST=host.docker.internal` is used from non-container host-side runs.
  - prints actionable guidance:
    - use `SKYNET_ENV_FILE=.env.local-e2e` (`127.0.0.1:22`) on worker laptop host runs, or
    - run `e2e_live.py` inside EC2 Dockerized gateway runtime.
- Upgraded `openclaw-gateway/tests/test_e2e_telegram_real_live.py` tracing:
  - step-level JSON trace events for send/wait/match/button-click/timeouts.
  - coding-loop message tracing with `complete=/failed=` extraction.
  - explicit run-phase success assertion (`Run Project` and exit-0 output).
- Updated live-E2E env templates and docs:
  - `.env.example` and `.env.local-e2e.example` now document `SKYNET_LIVE_E2E_FLOW` plus real Telegram credentials (`SKYNET_E2E_TELEGRAM_*`).
  - `README.md` now includes conversation vs `telegram_real` flow instructions and run commands.

### 2026-03-04 Real Telegram Live-E2E Mode Update

- Added a fully real Telegram network flow to live E2E:
  - new selector `SKYNET_LIVE_E2E_FLOW=telegram_real`
  - new test `openclaw-gateway/tests/test_e2e_telegram_real_live.py::test_real_telegram_chat_flow_no_github_repo_creation`
- Extended `openclaw-gateway/tests/e2e_live.py` to route to the real Telegram test target when `telegram_real` mode is selected.
- Kept existing conversation-mode live E2E intact; no mock or fake transport added in the real mode path.
- Added explicit no-repo-creation assertion in the real Telegram flow so E2E follows the "skip GitHub setup" branch.

### 2026-03-04 SSH-Primary Reliability Hardening Update

- Implemented SSH executor resilience controls in `openclaw-gateway/ssh_tunnel_executor.py`:
  - error categorization (`unreachable|auth|capacity|banner|timeout|unknown`)
  - `OPENCLAW_SSH_MAX_PARALLEL` bounded concurrency gate
  - category-aware retry backoff
  - circuit breaker with failure streak tracking
  - diagnostics API (`get_diagnostics`) exposing health/category/streak/circuit/endpoint
- Hardened forced SSH behavior:
  - `gateway.send_action` now fails fast when `OPENCLAW_EXECUTION_MODE=ssh_tunnel` but SSH is not configured.
  - Added route decision telemetry (`action`, `execution_mode`, `ssh_selected_reason`).
- Extended `/status` contract in `openclaw-gateway/api.py`:
  - `execution_mode_effective`
  - `ssh_health_ok`
  - `ssh_error_category`
  - `ssh_failure_streak`
  - `ssh_circuit_open_until`
  - `ssh_endpoint`
- Removed live-E2E false-green conditions:
  - `test_e2e_conversation_live.py` now uses `_skip_or_fail_live(...)` and fails when `SKYNET_E2E_FAIL_ON_SKIP=1`.
  - `tests/e2e_live.py` now forwards `SKYNET_E2E_FAIL_ON_SKIP`, emits `pytest_passed/pytest_skipped/infra_category`, and fails strict skip runs.
- Added SSH profile templates:
  - `.env.ec2-gateway.example`
  - `.env.local-e2e.example`
- Standardized tunnel lifecycle scripts:
  - rewrote `scripts/keep_tunnel_alive.ps1` with single-instance mutex, `ConnectTimeout`, log rotation, and heartbeat bind checks
  - added `scripts/register_tunnel_task.ps1`
  - added `scripts/check_tunnel_health.ps1`
- Added CI/CD SSH guardrails in `.github/workflows/deploy-ec2-skynet.yml`:
  - SSH config validation step
  - container-context SSH banner smoke
  - `/status` diagnostics contract assertion

### 2026-03-04 Codex-Primary Runtime Update

- Switched coding execution to stage-chain orchestration with explicit order support:
  - `codex -> claude_ollama -> cline` (config-driven via `SKYNET_CODING_FALLBACK_CHAIN`).
- Added force-all-projects runtime override:
  - `SKYNET_CODING_FORCE_PRIMARY_FOR_ALL=1` makes effective coding profile `codex_primary` for all projects.
- Added chain-aware coding preflight:
  - validates agent availability for configured stages via `check_coding_agents`.
  - allows fallback continuation when primary is unavailable and downstream stage is available.
- Added structured coding stage telemetry:
  - `coding.stage.start`
  - `coding.stage.fail`
  - `coding.stage.success`
- Updated strict quality auto-fix pass to use the same stage-chain routing (not hardcoded claude).
- Added codex-first milestone extraction with router fallback:
  - uses `run_coding_agent(agent=codex)` first.
  - falls back to router planner extraction on action/parse failure.
  - emits `milestone.primary.failover` logs on fallback.
- Updated project specialist/planner flow to codex-first with router fallback:
  - uses a planner sandbox path under project base (`_planner_sessions/<user_id>`).
  - enforces prompt constraints ("no file writes", "plain chat output only").
  - emits `planner.primary.failover` logs on fallback.
- Extended project profile normalization to support `coding_profile=codex_primary`.
- CI deploy workflow updated to pass new codex-primary/planner env vars.


### 2026-03-02 Deploy-Unblock Update

- Added coding profile defaults for new projects: `claude_ollama` + strict quality gates.
- Standardized `run_coding_agent` contract across gateway/worker to include `agent`, `backend`, `model`, and `auto_pull_model`.
- Added SSH-first routing for coding actions and explicit Claude-over-Ollama backend mode.
- Implemented Ollama model preflight + one-time auto-pull and explicit setup errors for missing/unreachable models.
- Preserved strict run-contract behavior and project-scoped run cache keys in coding/run handlers.
- Added fallback behavior: only after non-infra gate failure, use `claude` native backend when `ANTHROPIC_API_KEY` is present; otherwise fail with `FALLBACK_UNAVAILABLE`.

### 2026-03-03 Deploy-Reliability Update

- Added coding-session preflight in Telegram coding loop to fail fast when Claude CLI is unavailable, instead of burning milestone attempts.
- Added deterministic Telegram chat simulation test that reproduces claude-missing setup failures before milestone execution.
- Fixed deploy/runtime env drift by wiring SKYNET_CLAUDE_OLLAMA_* vars in CI and adding backward-compatible OPENCLAW_OLLAMA_* fallbacks in gateway/agent config.
- Set safer model/autopull defaults for fallback paths so runtime does not silently drift to large model pulls.
- Fixed milestone approval race by registering the approval event before sending milestone buttons, preventing fast taps from being dropped as "no active milestone".
- Added heartbeat progress updates during long `run_coding_agent` calls so users can see coding is still active.

### 2026-03-03 SSH-PTY + Live-Trace Update

- Root-caused long `run_coding_agent` waits on Windows SSH path: Claude CLI blocks on non-PTY Paramiko channels.
- Updated SSH executor command runner to support optional PTY allocation and enabled PTY specifically for Claude native and Claude+Ollama execution paths.
- Added ANSI/control-sequence cleanup in PowerShell output sanitization so PTY output remains readable in logs and summaries.
- Reworked `openclaw-gateway/tests/e2e_live.py` into a structured JSONL live trace runner (`SKYNET_LIVE_TRACE_FILE` override + periodic action wait heartbeats).
- Expanded `openclaw-gateway/tests/test_e2e_conversation_live.py` with step/action/heartbeat trace logging to make Telegram live E2E stalls diagnosable.

### 2026-03-03 Engineering-Policy + Strict-Gates Stabilization

- Root-caused deploy failures in GitHub Actions (`Path Guard -> Check engineering policy`) to missing `docs/AGENT_HANDOFF.md` update in commits that changed code paths.
- Added strict-gate reliability fixes so missing Python tooling no longer causes hard false negatives:
  - Auto-install `ruff` and retry lint once when unavailable.
  - Auto-install `pytest` and retry tests once when unavailable.
  - Auto-bootstrap `tests/test_smoke.py` for Python strict-mode projects when no tests are detected.
- Confirmed full live conversation flow (`hi` -> project creation -> coding -> run project) passes with strict gates enabled using local SSH profile.
- Hardened provider registration for optional SDK absence and improved live E2E env loading (`SKYNET_ENV_FILE`).

### 2026-03-04 Live-E2E Strict-Recovery Determinism Update

- Removed mock-based harnessing from live conversation E2E and now execute the real planner/coding/SSH/GitHub path only.
- Fixed strict recovery marker extraction to preserve required output tokens (for example `SKYNET_LIVE_E2E_OK`) during emergency scaffold fallback.
- Added milestone extraction heartbeat and timeout handling to avoid silent "stuck" behavior before milestone rendering.
- Persisted original user requirement snippets alongside approved plans so strict recovery and run-contract enforcement retain exact user constraints.
## Current Repo State

- Branch: `main`
- Multi-provider coding: router tries Gemini/Groq/Claude first, falls back to Ollama SSH
- Ollama `num_ctx` configurable via `OLLAMA_NUM_CTX` env var (default 8192, was 4096)
- GitHub Actions self-hosted runner on EC2 handles build + deploy via `docker compose`

## What Was Completed

### Coding Agent Improvements (Layer 1-4)

- **Router-based coding**: coding loop tries `router.chat(task_type="coding")` first (Gemini -> Groq -> Claude), parses fenced code blocks, writes files via SSH. Falls back to Ollama SSH if no cloud provider available.
- **Code context between milestones**: milestone N>1 reads previously written files via `file_read` and includes them in the prompt ("build on these, do NOT rewrite unchanged code").
- **Auto-retry**: Ollama SSH path retries up to 3 times if no files generated or non-zero exit code.
- **Better prompts**: removed dead `skynet_run.json` manifest prompt; added project-name file naming rule; shared `_CODING_SYSTEM_PROMPT` constant.
- **Configurable `num_ctx`**: `OLLAMA_NUM_CTX` env var (default 8192, up from hardcoded 4096).
- **Code block parser**: extracted `_parse_code_blocks()` - handles both filename-tagged and language-tagged fenced blocks with fallback naming.
- **Dead code cleanup**: removed `_RUN_MANIFEST_FILENAME`, `_ALLOWED_RUN_INTERPRETERS`, `_find_run_manifest()`, `_resolve_run_manifest()`, `_normalize_interpreter_name()`, `_build_run_command()`, `_is_safe_relative_path()`, `_is_safe_cli_token()`.

### Previous Fixes

- Fixed "Run Project" file-not-found error: SSH executor returns `files_written`; coding loop stores in `bot_data[run_files_{uid}]`
- Fixed "No existing session" SSH error: SFTP warmup after connect
- Fixed "Connection reset by peer" SSH banner error: retry loop with exponential backoff
- Fixed Python module shadowing bug: system prompt rule + post-generation rename
- Added plan validation and milestone generation fallback
- Hardened CI/CD: env vars, SSH key guard, gateway health check, `.dockerignore`

## Test Results

- `python -m py_compile openclaw-gateway/db/schema.py openclaw-gateway/tests/test_runtime_trace_forensic.py`
  - pass
- `python -m pytest openclaw-gateway/tests/test_runtime_trace.py openclaw-gateway/tests/test_runtime_trace_forensic.py openclaw-gateway/tests/test_ssh_executor_trace_forensics.py openclaw-gateway/tests/test_trace_command.py openclaw-gateway/tests/test_tracker_progress.py -q`
  - `14 passed`
- `python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD`
  - pass
- `python -m py_compile openclaw-gateway/bot/handlers/coding.py openclaw-gateway/bot/handlers/project.py openclaw-gateway/runtime_trace.py openclaw-gateway/ssh_tunnel_executor.py openclaw-gateway/tests/test_runtime_trace.py openclaw-gateway/tests/test_runtime_trace_forensic.py openclaw-gateway/tests/test_ssh_executor_trace_forensics.py openclaw-gateway/tests/test_tracker_progress.py`
  - pass
- `python -m pytest openclaw-gateway/tests/test_runtime_trace.py openclaw-gateway/tests/test_runtime_trace_forensic.py openclaw-gateway/tests/test_ssh_executor_trace_forensics.py openclaw-gateway/tests/test_trace_command.py openclaw-gateway/tests/test_tracker_progress.py -q`
  - `8 passed`
- `python scripts/ci/check_stale_paths.py`
  - pass
- `python scripts/ci/check_control_plane_boundary.py`
  - pass
- `python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD`
  - pass after updating `docs/AGENT_HANDOFF.md`

- `python -m pytest -q openclaw-gateway/tests/test_live_e2e_trace_logging.py openclaw-gateway/tests/test_live_e2e_runner_policy.py openclaw-gateway/tests/test_ssh_executor_resilience.py openclaw-gateway/tests/test_planner_resilience.py`
  - `26 passed`
- `python -m py_compile openclaw-gateway/tests/e2e_live.py openclaw-gateway/tests/test_e2e_telegram_real_live.py`
  - pass
- `python -m py_compile openclaw-gateway/ssh_tunnel_executor.py openclaw-gateway/api.py openclaw-gateway/gateway.py openclaw-gateway/tests/e2e_live.py openclaw-gateway/tests/test_e2e_conversation_live.py openclaw-gateway/tests/test_ssh_executor_resilience.py openclaw-gateway/tests/test_api_status_diagnostics.py openclaw-gateway/tests/test_gateway_ssh_mode.py openclaw-gateway/tests/test_live_e2e_runner_policy.py`
  - pass
- `python -m pytest -q openclaw-gateway/tests/test_ssh_executor_resilience.py openclaw-gateway/tests/test_api_status_diagnostics.py openclaw-gateway/tests/test_gateway_ssh_mode.py openclaw-gateway/tests/test_live_e2e_runner_policy.py`
  - `14 passed`
- `python -m pytest -q openclaw-gateway/tests/test_e2e.py openclaw-gateway/tests/test_telegram_chat_simulation.py openclaw-gateway/tests/test_project_planner_fallback.py`
  - `26 passed`
- `python -m pytest -q openclaw-gateway/tests/test_e2e.py openclaw-gateway/tests/test_telegram_chat_simulation.py openclaw-gateway/tests/test_project_planner_fallback.py openclaw-gateway/tests/test_ssh_executor_resilience.py openclaw-gateway/tests/test_api_status_diagnostics.py openclaw-gateway/tests/test_gateway_ssh_mode.py openclaw-gateway/tests/test_live_e2e_runner_policy.py`
  - `40 passed`
- `python scripts/ci/check_stale_paths.py`
  - pass
- `python scripts/ci/check_control_plane_boundary.py`
  - pass
- `python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD`
  - pass
- `pytest -q openclaw-gateway/tests/test_coding_retry.py openclaw-gateway/tests/test_telegram_chat_simulation.py openclaw-gateway/tests/test_project_planner_fallback.py`
  - `16 passed` (Paramiko deprecation warnings only).
- `python scripts/ci/check_stale_paths.py`
  - pass
- `python scripts/ci/check_control_plane_boundary.py`
  - pass
- `python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD`
  - initially failed due missing `docs/AGENT_HANDOFF.md` in change set; resolved after updating handoff doc.


- `python -m pytest openclaw-gateway/tests -q`
  - `37 passed`
- `docker exec openclaw-gateway python tests/e2e_live.py`
  - `ALL STEPS PASSED` (9.7s with 7b model)
- Ollama benchmark: 59.3 tok/s, 100% GPU (4.9GB/8GB VRAM)
- `pytest -q openclaw-agent/tests/test_executor.py openclaw-gateway/tests/test_bot_ui_contract.py openclaw-gateway/tests/test_coding_retry.py openclaw-gateway/tests/test_e2e.py openclaw-gateway/tests/test_integration.py`
  - `54 passed, 1 skipped`

- python -m py_compile openclaw-gateway/config.py openclaw-agent/executor/actions.py
  - pass
- python -m pytest openclaw-gateway/tests/test_telegram_chat_simulation.py openclaw-gateway/tests/test_coding_retry.py openclaw-gateway/tests/test_e2e.py -q
  - 29 passed
- python -m pytest openclaw-agent/tests/test_executor.py -q
  - 19 passed
- python -m pytest openclaw-gateway/tests/test_coding_retry.py -q
  - 7 passed
- python -m pytest openclaw-gateway/tests/test_telegram_chat_simulation.py openclaw-gateway/tests/test_e2e.py -q
  - 24 passed
- python -m py_compile openclaw-gateway/ssh_tunnel_executor.py openclaw-gateway/tests/e2e_live.py openclaw-gateway/tests/test_e2e_conversation_live.py
  - pass
- python -m pytest openclaw-gateway/tests/test_coding_retry.py openclaw-gateway/tests/test_e2e.py -q
  - 32 passed
- python -m pytest openclaw-gateway/tests/test_e2e_conversation_live.py -q
  - 1 skipped (requires `SKYNET_E2E_LIVE=1`)
- `python -m pytest -q openclaw-gateway/tests/test_coding_retry.py`
  - `9 passed`
- `python -m pytest -q openclaw-gateway/tests/test_conversation_e2e_repo_push.py`
  - `1 passed`
- `$env:SKYNET_ENV_FILE='.env.local-e2e'; python openclaw-gateway/tests/e2e_live.py`
  - `1 passed` (`test_live_conversation_real_planner_codegen_and_github_push`, ~236s)
- `$env:SKYNET_ENV_FILE='.env.local-e2e'; python openclaw-gateway/tests/e2e_live.py`
  - `1 passed` (`test_live_conversation_real_planner_codegen_and_github_push`, ~143s, trace `e2e-live-1772610595.log`)
## Trace Evidence

- request_id=deploy-repair-runtime-trace-schema-order-20260306
- task_id=runtime-trace-legacy-migration-index-fix
- GitHub Actions run `22769176004`
- skynet.trace.log
- request_id=max-forensic-runtime-trace-20260306
- task_id=ssh-first-live-e2e-trace-expansion
- skynet.trace.log
- /trace deep
- session_key runtime correlation added to SSH coding actions
- request_id=telegram-tracker-cleanup-20260306
- task_id=ssh-status-tracker-and-chat-normalization
- `E:\MyProjects\skynet\logs\skynet.trace.log`
- request_id=live-e2e-runtime-trace-visibility-20260305
- task_id=telegram-real-e2e-runtime-trace-snapshots
- `E:\MyProjects\skynet\logs\e2e-live-1772737353.log`
- `E:\MyProjects\skynet\logs\skynet.trace.log`
- Live e2e test run via `tests/e2e_live.py` inside container
- request_id=ci-cd-hardening-20260302
- task_id=ci-cd-env-vars-and-guards
- skynet.trace.log
- audit.jsonl
- request_id=claude-ollama-revamp-20260302
- task_id=strict-gates-ssh-first-rollout

- request_id=telegram-coding-stall-20260303
- task_id=deploy-env-alignment-and-preflight
- request_id=ssh-pty-live-trace-20260303
- task_id=claude-ssh-pty-stall-fix
- skynet.trace.log
- openclaw-gateway/tests/.artifacts/live-postfix-20260303-114629.log
- request_id=strict-gates-stabilization-20260303
- task_id=live-conversation-e2e-policy-compliance
- openclaw-gateway/tests/.artifacts/e2e-live-1772550136.log
- openclaw-gateway/tests/.artifacts/e2e-live-1772550666.log
- request_id=live-e2e-strict-recovery-20260304
- task_id=conversation-flow-no-mocks
- openclaw-gateway/tests/.artifacts/e2e-live-1772610595.log
- request_id=ssh-primary-reliability-hardening-20260304
- task_id=ssh-tunnel-resilience-and-status-contract
- /v1/events
## Documentation Updates

- `docs/AGENT_HANDOFF.md`
- `.github/workflows/deploy-ec2-skynet.yml`
- `README.md`
- `docs/DEBUG_PLAYBOOK.md`
- `docs/IMPLEMENTATION_GUIDE.md`
- `.gitignore`
- `.env.example`
- `.env.ec2-gateway.example`
- `.env.local-e2e.example`

## Blockers

- None. CI/CD handles full deploy cycle.

## Required Next Steps

1. Test end-to-end: create a 2+ milestone project via Telegram, verify milestone 2 prompt includes milestone 1 code.
2. Verify router-based coding activates when Gemini/Groq keys are set.
3. Monitor code quality across providers; tune `TASK_PROVIDER_PREFERENCES["coding"]` ordering.
4. Consider bumping Ollama model to 14b/32b if local quality is insufficient.

## Policy Checklist

- [x] Documentation updated for behavior/interface/ops changes.
- [x] Tests run, commands listed, and outcomes recorded.
- [x] Trace evidence recorded for behavior changes (request/task/trace markers).
- [x] Guard scripts executed (`check_stale_paths`, `check_control_plane_boundary`, `check_engineering_policy`).
