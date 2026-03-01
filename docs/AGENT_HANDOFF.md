# Agent Handoff

Last updated (UTC): 2026-03-01

## Current Goal

Implement a coding-agent documentation pack and CI-enforced engineering policy (docs + tests + tracing evidence).

## Current Repo State

- Branch: `main`
- Working tree baseline includes runtime artifact change at `openclaw-agent/logs/audit.jsonl`
- New policy/doc work targets markdown docs and CI check scripts only (no runtime API contract changes)

## What Was Completed

- Added documentation index and implementation/debug/policy references.
- Added current-code-first drift and test-matrix documentation.
- Added engineering policy definitions for documentation, testing, and tracing evidence.
- Added policy enforcement script: `scripts/ci/check_engineering_policy.py`.
- Added policy checker unit tests: `tests/test_ci_engineering_policy.py`.
- Wired policy checks into:
  - `Makefile` (`check-policy`, `smoke`)
  - `scripts/dev/smoke.py`
  - `.github/workflows/deploy-ec2-skynet.yml` guard job

## Test Results

- `python -m pytest tests/test_ci_engineering_policy.py -q`
  - `5 passed`
- `python scripts/ci/check_stale_paths.py`
  - `No stale path references found.`
- `python scripts/ci/check_control_plane_boundary.py`
  - `Control-plane boundary check passed.`
- `python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD`
  - `Engineering policy check passed.`
- `python scripts/dev/smoke.py`
  - `Smoke checks passed.`
- `python -m pytest tests/test_api_lifespan.py tests/test_api_provider_config.py tests/test_api_control_plane.py tests/test_job_locking.py tests/test_worker_registry.py -q`
  - `11 passed`
- `python -m pytest openclaw-gateway/tests -q`
  - `21 passed, 3 warnings`
- `python -m pytest openclaw-agent/tests -q`
  - `29 passed, 1 skipped`

## Trace Evidence

- Policy checker requires trace markers for behavior changes and validates marker presence in handoff.
- Marker references used for policy compliance and docs examples:
  - `request_id=policy-docs-20260301`
  - `task_id=docs-policy-pack`
  - `claim_token=policy-checker-v1`
  - `openclaw-agent/logs/audit.jsonl`
  - `skynet.trace.log`
  - `/v1/events`

## Documentation Updates

- `docs/INDEX.md`
- `docs/ARCHITECTURE_MAP.md`
- `docs/IMPLEMENTATION_GUIDE.md`
- `docs/DEBUG_PLAYBOOK.md`
- `docs/KNOWN_DRIFT_AND_TEST_MATRIX.md`
- `docs/ENGINEERING_POLICY.md`
- `README.md`
- `AGENT_GUIDE.md`
- `docs/AGENT_HANDOFF.md`

## Blockers

- None identified at this stage.

## Required Next Steps

1. If runtime behavior changes are introduced next, attach real run identifiers (`request_id`, `task_id`, `/v1/events` rows) for that change.
2. Keep `docs/KNOWN_DRIFT_AND_TEST_MATRIX.md` updated as legacy `core.*` drift is reduced.

## Policy Checklist

- [x] Documentation updated for behavior/interface/ops changes.
- [x] Tests run, commands listed, and outcomes recorded.
- [x] Trace evidence recorded for behavior changes (request/task/trace markers).
- [x] Guard scripts executed (`check_stale_paths`, `check_control_plane_boundary`, `check_engineering_policy`).
