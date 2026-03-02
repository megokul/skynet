# Agent Handoff

Last updated (UTC): 2026-03-02

## Current Goal

Optimize Ollama coding pipeline: switch to 7b model for full GPU acceleration, fix code block filename parsing.

## Current Repo State

- Branch: `main`
- Ollama coding agent fully operational via SSH tunnel (EC2 → laptop)
- Container running with `qwen2.5-coder:7b`, live e2e tests passing

## What Was Completed

- Switched Ollama model from `qwen2.5-coder:32b-instruct-q4_K_M` to `qwen2.5-coder:7b` for full GPU acceleration (59 tok/s vs 3 tok/s).
- Set `OLLAMA_FLASH_ATTENTION=1` as machine-level env var on laptop.
- Fixed code block parser: 7b model outputs ` ```python ` instead of ` ```filename.py `. Added fallback that maps language tags to file extensions (e.g. `python` → `main.py`).
- Improved system prompt with explicit examples telling model to use filenames, not language names.
- Tested and reverted `num_gpu=99` override which degraded performance (forced 19GB into slow GPU shared memory → 0.5 tok/s).
- Updated `.env`, `docker-compose.yml`, and `ssh_tunnel_executor.py` defaults.
- Rebuilt and redeployed container on EC2 with correct model and `--add-host` flag.

## Test Results

- `python -m pytest openclaw-gateway/tests -q`
  - `36 passed, 1 skipped`
- `docker exec openclaw-gateway python tests/e2e_live.py`
  - `ALL STEPS PASSED` (9.7s with 7b model)
  - Step 1: create_directory OK
  - Step 2: run_coding_agent OK (Wrote 1 file: main.py)
  - Step 3: file_read verify OK
  - Step 4: exec_command OK (output: SKYNET_E2E_OK)
- Ollama benchmark: 59.3 tok/s, 100% GPU (4.9GB/8GB VRAM), load time 3.2s

## Trace Evidence

- Live e2e test run via `tests/e2e_live.py` inside container
- request_id=ollama-7b-optimization-20260302
- task_id=perf-ollama-7b-switch
- skynet.trace.log
- audit.jsonl

## Documentation Updates

- `docs/AGENT_HANDOFF.md`

## Blockers

- EC2 repo uses HTTPS without credentials; `git pull` fails. Files must be copied via `scp` and image rebuilt manually.

## Required Next Steps

1. Fix EC2 git credentials (switch to SSH remote or configure GH_TOKEN for HTTPS).
2. Monitor 7b code quality on real Telegram projects; consider 14b if quality drops.

## Policy Checklist

- [x] Documentation updated for behavior/interface/ops changes.
- [x] Tests run, commands listed, and outcomes recorded.
- [x] Trace evidence recorded for behavior changes (request/task/trace markers).
- [x] Guard scripts executed (`check_stale_paths`, `check_control_plane_boundary`, `check_engineering_policy`).
