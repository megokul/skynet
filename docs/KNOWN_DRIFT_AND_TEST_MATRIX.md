# Known Drift and Test Matrix

Current-code-first reference for known drift and authoritative test execution.

## Current Baseline

- Date captured: 2026-03-12
- Branch: `main`
- Runtime artifacts should be ignored or kept under placeholder-only directories. Tracked runtime logs are policy violations.
- Curated suites validated in this environment:
  - `python -m pytest openclaw-gateway/tests -q`
  - `python -m pytest openclaw-agent/tests -q`
  - `python -m pytest tests/test_api_lifespan.py tests/test_api_provider_config.py tests/test_api_control_plane.py tests/test_job_locking.py tests/test_task_queue_control_plane.py tests/test_worker_registry.py tests/test_ci_engineering_policy.py tests/test_project_documentation_skill.py tests/test_prompt_references.py -q`

## Known Drift

1. Legacy path references remain in a few modules/comments for modules not present in this checkout:
- `openclaw-gateway/bot/commands.py`
- `openclaw-gateway/core/conversation_manager.py`
- `openclaw-gateway/core/*`

2. Some modules reference `core.prompt_library`:
- `openclaw-gateway/ai/prompts.py`
- `openclaw-gateway/ai/context.py`

Implication:

- Root `tests/` is curated and invoked explicitly.
- `tests/README.md` is the local guide for what may live there.
- Do not use broad `pytest tests/` sweeps as the authoritative signal for merge-readiness.
- Use the authoritative matrix below unless the task explicitly expands scope.

## Authoritative Test Matrix

Control plane:

```bash
python -m pytest tests/test_api_lifespan.py tests/test_api_provider_config.py tests/test_api_control_plane.py tests/test_job_locking.py tests/test_task_queue_control_plane.py tests/test_worker_registry.py tests/test_ci_engineering_policy.py tests/test_project_documentation_skill.py tests/test_prompt_references.py -q
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
python scripts/ci/check_settings_policy.py
python scripts/ci/check_repo_hygiene.py
python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD
```

## Legacy or Optional Suites

Broad `pytest tests/` runs are intentionally non-authoritative for this checkout.

Known failure signature:

```text
ModuleNotFoundError: No module named 'core'
```

Use the targeted suites above as authoritative unless the task explicitly includes refactoring legacy `core.*` compatibility paths.
