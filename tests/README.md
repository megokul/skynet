# Root Test Suite

This directory is a curated root-level suite.

Authoritative rule:
- default pytest discovery does not recurse into `tests/`
- these tests are invoked explicitly by `make test-control-plane`, `make test-all`, and `scripts/dev/smoke.py`
- the authoritative root entrypoint is `python -m skynet.test_matrix --run`

Keep only:
- control-plane contract and scheduler tests
- repo-policy / docs / prompt-reference checks

Do not add here:
- gateway behavior tests
- agent behavior tests
- live E2E tests
- legacy compatibility tests for removed `core.*` paths

Current curated files:
- `test_api_lifespan.py`
- `test_api_provider_config.py`
- `test_api_control_plane.py`
- `test_job_locking.py`
- `test_task_queue_control_plane.py`
- `test_worker_registry.py`
- `test_ci_engineering_policy.py`
- `test_ci_repo_hygiene.py`
- `test_project_documentation_skill.py`
- `test_prompt_references.py`
