# Agent Handoff

Last updated (UTC): 2026-03-02

## Current Goal

Harden CI/CD pipeline so pushing to main is the only deploy step needed — no manual SCP, SSH, or docker commands.

## Current Repo State

- Branch: `main`
- Ollama coding agent operational via SSH tunnel (EC2 → laptop) with `qwen2.5-coder:7b`
- GitHub Actions self-hosted runner on EC2 handles build + deploy via `docker compose`

## What Was Completed

- Added missing env vars to CI workflow: `GH_TOKEN`, `OPENCLAW_OLLAMA_URL`, `OPENCLAW_OLLAMA_MODEL`, logging vars
- Added env vars to `.env.ci` build step with correct defaults
- Added `GH_TOKEN` to `docker-compose.yml` for GitHub repo creation on laptop
- Added SSH key file guard: removes stale directory before decoding, verifies result is a file
- Added gateway health check: polls `http://localhost:8766/status` after deploy
- Fixed stale defaults: `AI_PROVIDER_PRIORITY` removed `ollama`, `OPENCLAW_SSH_ALLOWED_ROOTS` → `E:\SKYNET-SANDBOX`
- Added `.dockerignore` to prevent stale `data/skynet.db` from being baked into images
- Fixed HTML escaping in Telegram bot output (prevents `<module>` parse errors)
- Improved Ollama system prompt and code block parser for 7b model compatibility
- Added plan validation: retries if AI outputs meta-response instead of real plan
- Added milestone generation fallback: generates milestones from project info when plan text has none
- Fixed Python module shadowing bug: `blakely.py` + `blakely/utils.py` conflict. Added system prompt rule + post-generation rename (`foo/` → `lib/`) with import rewriting
- Fixed "No existing session" SSH error: added SFTP warmup after connect() and close/reopen SFTP around long-running exec_command

## Test Results

- `python -m pytest openclaw-gateway/tests -q`
  - `36 passed, 1 skipped`
- `docker exec openclaw-gateway python tests/e2e_live.py`
  - `ALL STEPS PASSED` (9.7s with 7b model)
- Ollama benchmark: 59.3 tok/s, 100% GPU (4.9GB/8GB VRAM)

## Trace Evidence

- Live e2e test run via `tests/e2e_live.py` inside container
- request_id=ci-cd-hardening-20260302
- task_id=ci-cd-env-vars-and-guards
- skynet.trace.log
- audit.jsonl

## Documentation Updates

- `docs/AGENT_HANDOFF.md`

## Blockers

- None. CI/CD handles full deploy cycle.

## Required Next Steps

1. Monitor 7b code quality on real Telegram projects; consider 14b if quality drops.
2. Add deploy success notification to Telegram (optional).

## Policy Checklist

- [x] Documentation updated for behavior/interface/ops changes.
- [x] Tests run, commands listed, and outcomes recorded.
- [x] Trace evidence recorded for behavior changes (request/task/trace markers).
- [x] Guard scripts executed (`check_stale_paths`, `check_control_plane_boundary`, `check_engineering_policy`).
