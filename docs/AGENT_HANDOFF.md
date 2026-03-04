# Agent Handoff

Last updated (UTC): 2026-03-04 15:05

## Current Goal

SSH-primary execution reliability hardening across gateway, live E2E, tunnel lifecycle, and deployment diagnostics, while preserving strict quality gates and deterministic run behavior.

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
