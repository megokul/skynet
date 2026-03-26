# Code Quality Audit

Current high-drift areas and the cleanup direction for each.

## Active Hotspots

1. `openclaw-gateway/ssh_tunnel_executor.py`
   - Problem: still mixes remote execution policy, session lifecycle, coding-agent orchestration, and diagnostics.
   - Current pass: SSH config/support helpers, circuit-breaker diagnostics, and runtime session/trace bookkeeping are now extracted; template docstring debt was cleared and the temporary allowlist removed.
   - Next pass: keep splitting remote file operations, command flow, and coding-agent orchestration away from the executor core.

2. `openclaw-agent/executor/actions.py`
   - Problem: action registry, subprocess lifecycle, and coding/session orchestration are still co-located.
   - Current status:
     - Qwen execution moved into `executor/qwen_runner.py`
     - subprocess helpers now live in `executor/action_support.py`
     - filesystem/archive helpers now live in `executor/action_fs.py`
     - search helpers now live in `executor/action_search.py`
     - git/build/dev helpers now live in `executor/action_process.py`
     - runtime session tracking now lives in `executor/runtime_sessions.py`
   - Next pass: extract the remaining coding-agent orchestration once the current monkeypatch seams are narrowed.

3. `openclaw-gateway/bot/handlers/coding.py`
   - Problem: still a very large orchestration surface.
   - Current status: stage execution, tracker state, transport helpers, milestone planning, tracker lifecycle, stop-cleanup tracing, and shared milestone contract logic are now split into helper modules.
   - Next pass: finish peeling the remaining legacy milestone loop and other oversized orchestration branches into narrower seams.

4. `openclaw-gateway/db/store.py`
   - Problem: the facade still exports a large persistence surface.
   - Current pass: shared JSON codecs plus focused `store_memory.py`, `store_runtime_trace.py`, and `store_worker_policy.py` now own extracted domains.
   - Next pass: continue moving architecture/task-strategy/learning helpers behind the same facade pattern.

5. `skynet/ledger/task_queue.py`
   - Problem: queue transitions, JSON decoding, event writes, and graph checks still meet in one class.
   - Current pass: JSON parsing, event writes, and cycle detection now live in `task_queue_support.py`.
   - Next pass: continue extracting ownership and transition concerns behind narrower collaborators.

## Current Pass

This cleanup pass stayed behavior-frozen and focused on low-risk truth-alignment plus structural debt reduction:

- one authoritative root test entrypoint now exists: `python -m skynet.test_matrix --run`
- engineering policy now has explicit `baseline` and `strict` modes
- gateway logging now splits sink implementations (`logging_handlers.py`) and path helpers (`logging_targets.py`) from bootstrap wiring
- gateway milestone planning now has an internal helper seam in `bot/handlers/coding_planning.py`
- gateway store domains now split into `store_memory.py`, `store_runtime_trace.py`, and `store_worker_policy.py`
- control-plane queue helper logic now lives in `skynet/ledger/task_queue_support.py`
- docs and workflow commands now point at the same authoritative test/policy entrypoints
- `scripts/ci/check_engineering_policy.py` now enforces template-docstring debt with no temporary allowlist entries remaining
- SSH runtime session bookkeeping now lives in `openclaw-gateway/ssh_tunnel_sessions.py`
- the tracker lifecycle now lives behind `bot/handlers/coding_tracker_runtime.py`
- stop-request remote cancel cleanup now lives behind `bot/handlers/coding_stop_cleanup.py`
- the permanently skipped legacy gateway integration test was retired in favor of deterministic `test_e2e.py` coverage
- control-loop milestone execution now runs on normalized structured milestone contracts with `deliverables`, `acceptance`, `required_for_completion`, and `satisfaction_checks`
- already-satisfied milestones can now be detected before provider execution instead of burning live quota on redundant work
- stage execution now classifies quota/rate-limit failures before any `coding.stage.success` event is emitted

## Debt Snapshot

- Remaining oversized modules to treat as architectural refactor targets:
  - `openclaw-gateway/ssh_tunnel_executor.py`
  - `openclaw-gateway/bot/handlers/coding.py`
  - `openclaw-gateway/db/store.py`
  - `skynet/ledger/task_queue.py`
  - `openclaw-agent/executor/actions.py`

## Next Extraction Boundaries

- `openclaw-gateway/ssh_tunnel_executor.py`: isolate remote file ops, command execution, and coding-agent orchestration behind narrower helpers.
- `openclaw-gateway/bot/handlers/coding.py`: finish the remaining legacy milestone loop extraction and remove leftover dead delegation residue.
- `openclaw-gateway/db/store.py`: keep one facade but continue moving architecture/task-strategy/learning domains behind focused modules.
- `skynet/ledger/task_queue.py`: split file-ownership and transition authority from the manager facade.
- `openclaw-agent/executor/actions.py`: extract the remaining coding-agent orchestration once the current monkeypatch seams are replaced with narrower tests.
