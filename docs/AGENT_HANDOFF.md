# Agent Handoff

Last updated (UTC): 2026-03-03 11:50

## Current Goal

Supercharge the CLAW coding agent — smarter model routing, better prompts, auto-retry, and code context between milestones.

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
## Current Repo State

- Branch: `main`
- Multi-provider coding: router tries Gemini/Groq/Claude first, falls back to Ollama SSH
- Ollama `num_ctx` configurable via `OLLAMA_NUM_CTX` env var (default 8192, was 4096)
- GitHub Actions self-hosted runner on EC2 handles build + deploy via `docker compose`

## What Was Completed

### Coding Agent Improvements (Layer 1–4)

- **Router-based coding**: coding loop tries `router.chat(task_type="coding")` first (Gemini → Groq → Claude), parses fenced code blocks, writes files via SSH. Falls back to Ollama SSH if no cloud provider available.
- **Code context between milestones**: milestone N>1 reads previously written files via `file_read` and includes them in the prompt ("build on these, do NOT rewrite unchanged code").
- **Auto-retry**: Ollama SSH path retries up to 3 times if no files generated or non-zero exit code.
- **Better prompts**: removed dead `skynet_run.json` manifest prompt; added project-name file naming rule; shared `_CODING_SYSTEM_PROMPT` constant.
- **Configurable `num_ctx`**: `OLLAMA_NUM_CTX` env var (default 8192, up from hardcoded 4096).
- **Code block parser**: extracted `_parse_code_blocks()` — handles both filename-tagged and language-tagged fenced blocks with fallback naming.
- **Dead code cleanup**: removed `_RUN_MANIFEST_FILENAME`, `_ALLOWED_RUN_INTERPRETERS`, `_find_run_manifest()`, `_resolve_run_manifest()`, `_normalize_interpreter_name()`, `_build_run_command()`, `_is_safe_relative_path()`, `_is_safe_cli_token()`.

### Previous Fixes

- Fixed "Run Project" file-not-found error: SSH executor returns `files_written`; coding loop stores in `bot_data[run_files_{uid}]`
- Fixed "No existing session" SSH error: SFTP warmup after connect
- Fixed "Connection reset by peer" SSH banner error: retry loop with exponential backoff
- Fixed Python module shadowing bug: system prompt rule + post-generation rename
- Added plan validation and milestone generation fallback
- Hardened CI/CD: env vars, SSH key guard, gateway health check, `.dockerignore`

## Test Results

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
## Documentation Updates

- `docs/AGENT_HANDOFF.md`

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
