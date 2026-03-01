# Known Drift and Test Matrix

Current-code-first reference for known drift and authoritative test execution.

## Current Baseline

- Date captured: 2026-03-01
- Branch: `main`
- Observed runtime artifact change in working tree: `openclaw-agent/logs/audit.jsonl`
- Curated suites validated in this environment:
  - `python -m pytest openclaw-gateway/tests -q`
  - `python -m pytest openclaw-agent/tests -q`
  - `python -m pytest tests/test_api_lifespan.py tests/test_api_provider_config.py tests/test_api_control_plane.py tests/test_job_locking.py tests/test_worker_registry.py -q`

## Known Drift

1. Legacy path references remain in tests and comments for modules not present in this checkout:
- `openclaw-gateway/bot/commands.py`
- `openclaw-gateway/core/conversation_manager.py`
- `openclaw-gateway/core/*`

2. `core.*` import expectations are still present in legacy tests:
- `tests/test_trace_logger.py`
- `tests/test_commander_engine.py`
- `tests/test_orchestrator_invariants.py`

3. Some modules reference `core.prompt_library`:
- `openclaw-gateway/ai/prompts.py`
- `openclaw-gateway/ai/context.py`

Implication:

- Do not assume all legacy `tests/` are green in this checkout.
- Use the authoritative matrix below for merge-readiness unless explicitly expanding scope.

## Authoritative Test Matrix

Control plane:

```bash
python -m pytest tests/test_api_lifespan.py tests/test_api_provider_config.py tests/test_api_control_plane.py tests/test_job_locking.py tests/test_worker_registry.py -q
```

Gateway:

```bash
python -m pytest openclaw-gateway/tests -q
```

Agent:

```bash
python -m pytest openclaw-agent/tests -q
```

Policy and guardrails:

```bash
python scripts/ci/check_stale_paths.py
python scripts/ci/check_control_plane_boundary.py
python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD
```

## Legacy or Optional Suites

Legacy broad `tests/` run can currently fail due stale `core` imports in this checkout.

Known failure signature:

```text
ModuleNotFoundError: No module named 'core'
```

Use targeted suites above as authoritative unless the task explicitly includes refactoring legacy `core.*` compatibility paths.
