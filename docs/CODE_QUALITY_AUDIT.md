# Code Quality Audit

Current high-drift areas and the cleanup direction for each.

## Active Hotspots

1. `openclaw-gateway/ssh_tunnel_executor.py`
   - Problem: mixed config parsing, path policy, command building, remote execution, and runtime diagnostics in one file.
   - Current pass: extract SSH config loading and path/command helpers into dedicated modules.
   - Next pass: split remote file operations and coding-agent orchestration away from the executor core.

2. `openclaw-agent/executor/actions.py`
   - Problem: action registry, filesystem helpers, subprocess lifecycle, runtime session tracking, and coding-agent orchestration are still co-located.
   - Current status:
     - Qwen execution moved into `executor/qwen_runner.py`
     - generic subprocess helpers now live in `executor/action_support.py`
     - filesystem/archive helpers now live in `executor/action_fs.py`
     - search helpers now live in `executor/action_search.py`
     - git/build/dev actions now live in `executor/action_process.py`
     - runtime session tracking now lives in `executor/runtime_sessions.py`
     - `executor/actions.py` is now primarily a facade plus coding/session orchestration
   - Next pass: extract coding-agent orchestration from the facade once the current monkeypatch seams are retired or replaced with narrower tests.

3. `openclaw-gateway/bot/handlers/coding.py`
   - Problem: still a large orchestration surface even after stage-policy, tracker-state, and transport extraction.
   - Current status: support logic is now split into helper modules.
   - Next pass: separate stage execution flow, tracker lifecycle, and terminal-state handling.

4. `openclaw-gateway/api.py`
   - Problem: status reporting, action execution, idempotency cache, and profile endpoints remain in one module.
   - Next pass: split route groups by concern while keeping one app factory.

5. `openclaw-gateway/logging_setup.py`
   - Problem: logging config, sink setup, local-file handling, websocket mirror wiring, and cloud log helpers still share one file.
   - Next pass: split formatter/sink config from runtime log-mirror coordination.

## Current Pass

This cleanup pass stayed behavior-frozen and focused on low-risk truth-alignment plus policy debt reduction:

- root/operator docs now match the checked-in settings for provider priority, orchestration defaults, control-loop profile, and websocket-vs-SSH defaults
- the repo-local project-documentation skill now uses supported control-plane reads and only enqueues tasks when `control/TASK_GRAPH.yaml` defines queue-compatible action metadata
- template-generated docstrings were removed from the cleaned runtime and support module set, including:
  - control-plane API, registry, scheduler, reaper, queue, worker registry, and lock manager modules
  - worker entry/config/router/validator plus smaller support modules
  - gateway action/status route helpers and smaller AI/search helpers
  - CI/support scripts touched in this pass
- `scripts/ci/check_engineering_policy.py` now ratchets template-docstring debt with an explicit temporary allowlist

## Debt Snapshot

- Remaining template-docstring marker count outside tests: `138`
- Temporary ratchet allowlist:
  - `openclaw-gateway/logging_setup.py`
  - `openclaw-gateway/ssh_tunnel_executor.py`
- Remaining oversized modules to treat as architectural refactor targets:
  - `openclaw-gateway/ssh_tunnel_executor.py`
  - `openclaw-gateway/logging_setup.py`
  - `openclaw-gateway/bot/handlers/coding.py`
  - `openclaw-gateway/api.py`
  - `openclaw-agent/executor/actions.py`

## Next Extraction Boundaries

- `openclaw-gateway/logging_setup.py`: split sink/bootstrap config from websocket/log-mirror runtime hooks.
- `openclaw-gateway/ssh_tunnel_executor.py`: isolate remote file ops, command execution, and coding-agent orchestration behind narrower helpers.
- `openclaw-gateway/bot/handlers/coding.py`: separate stage execution flow, tracker lifecycle, and terminal-state handling.
- `openclaw-gateway/api.py`: keep one app factory but move action/idempotency/profile wiring behind focused route modules.
- `openclaw-agent/executor/actions.py`: extract the remaining coding-agent orchestration once the current monkeypatch seams are replaced with narrower tests.
