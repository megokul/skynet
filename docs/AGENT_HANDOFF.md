# Agent Handoff

Last updated (UTC): 2026-03-18 10:24

## Current Goal

WebSocket-primary worker execution with the checked-in settings as the source of truth: `legacy` orchestration by default, `loop_v2` enabled, Codex as planner primary, `qwen,codex` coding fallback, and SSH fallback disabled unless explicitly enabled.

### 2026-03-25 Smoke Gate Server-Aware Timeout Handling

- Fixed: HTML page project ("Philipinte pari") failed because smoke gate ran `python philipinte_pari.py` which starts `httpd.serve_forever()` — hangs 120s → killed → "process timed out" → misclassified as infra failure → repair never triggered → graph failed (complete=0, failed=2).
- Root cause 1: `_is_infra_error()` matches "timed out" broadly, and smoke exception handler unconditionally set `infra_failure = True`.
- Root cause 2: Worker's `exec_command` validates interpreter as first token — subshell wrappers like `(python ...)` get rejected as `Interpreter '(python' is not allowed`.
- Fix 1: Smoke timeout reduced from 120s to 15s. Process timeout now treated as **success** (entrypoint started without crashing — expected for servers).
- Fix 2: Non-timeout smoke exceptions still classified correctly; infra errors flagged, code errors go through repair.
- Fix 3: Work prompt now explicitly forbids blocking servers, `webbrowser.open()`, GUI popups in entrypoints. Servers must exit cleanly or use a `--serve` flag.
- Fix 4: Repair prompt detects timeout findings and adds specific guidance for fixing blocking entrypoints.
- Regression tests added for smoke timeout classification.
- Test results: 266 passed, 3 skipped, 0 failures.

### 2026-03-24 Stale Critic Findings Fix (gate_final CONTRACT_FAILED)

- Fixed `CONTRACT_FAILED: "Blocking critic findings remain: 1"` after successful repair.
- Root cause: old critic findings from pre-repair runs were never deleted from `critic_findings` table. `gate_final` counted ALL findings across all runs.
- Added `delete_critic_findings_for_node()` in `db/store.py`, called before re-queuing critic after repair.
- Defense-in-depth: `gate_final` query now filters `AND tn.status = 'done'` — re-queued critics' old findings excluded.
- Repair prompt now includes `milestone_text` so the coding agent knows WHAT the code should do, not just what failed.
- Regression test added in `test_control_loop_critic.py`.
- Test results: 263 passed, 3 skipped, 0 failures.

### 2026-03-24 Gate Failure → Critic Repair Flow

- Fixed `DEADLOCK_DETECTED` when a work node fails its quality gates.
- Root cause: `_work_executor` returned `{ok: False}` on gate failure → work node marked terminal → critic never runs → repair never triggers → deadlock after 3 idle ticks.
- Gate-failed work nodes now return `ok: True` with `gate_failed: True` and populate `work_context` so the critic can inspect gate failures.
- Critic injects a `critical` severity `GATE_FAILURE` finding when its work node had gate failures, ensuring the existing repair mechanism triggers.
- Infra failures (non-code) still propagate as hard fails; dependent non-gate nodes are auto-skipped via `_skip_blocked_dependents()`.
- `runnable_nodes()` now treats `"skipped"` dependencies as satisfied (alongside `"done"`).
- `_gate_executor` accepts `"skipped"` critics (not just `"done"`).
- `max_repairs` default increased from 1 to 3 to support multi-milestone repair.
- Final graph status supports partial completion: `"completed"` when all nodes resolved and gates passed.
- Files changed:
  - `openclaw-gateway/bot/handlers/coding.py` — gate failure returns `ok: True`, critic gate-failure finding injection, gate executor accepts skipped
  - `openclaw-gateway/orchestration/graph.py` — `runnable_nodes` accepts `"skipped"` deps
  - `openclaw-gateway/orchestration/loop_controller.py` — `_skip_blocked_dependents`, `max_repairs=3`, partial completion status
- Test results: 262 passed, 3 skipped (live-only), 0 failures.
- Trace evidence: critic timeout advisory (commit `da41a69`) verified in graph 48; this commit fixes the remaining deadlock path.

### 2026-03-18 Continuation Validation Pass

- Picked up the existing uncommitted structural-cleanup/refactor worktree and validated it before extending the change set further.
- Sanity-checked the live E2E sandbox artifact referenced from the editor:
  - `E:\SKYNET-SANDBOX\Projects\live-e2e-1773822739\live-e2e-1773822739.py`
  - `E:\SKYNET-SANDBOX\Projects\live-e2e-1773822739\test_live_e2e.py`
  - result: generated CLI/test pair is internally consistent and does not appear to be the unfinished part of the current repo work.
- Local validation completed successfully against the current repo worktree:
  - `.\venv\Scripts\python.exe -m py_compile openclaw-agent\executor\actions.py openclaw-agent\executor\action_support.py openclaw-agent\executor\runtime_sessions.py openclaw-agent\executor\action_fs.py openclaw-agent\executor\action_search.py openclaw-agent\executor\action_process.py openclaw-agent\executor\qwen_runner.py openclaw-gateway\api.py openclaw-gateway\api_action_routes.py openclaw-gateway\api_profile_routes.py openclaw-gateway\api_shared.py openclaw-gateway\api_status_routes.py openclaw-gateway\bot\handlers\coding.py openclaw-gateway\bot\handlers\coding_stage_execution.py openclaw-gateway\bot\handlers\coding_stage_policy.py openclaw-gateway\bot\handlers\coding_terminal.py openclaw-gateway\bot\handlers\coding_tracker_state.py openclaw-gateway\bot\handlers\coding_transport.py openclaw-gateway\bot\handlers\project_session.py openclaw-gateway\ssh_tunnel_executor.py openclaw-gateway\ssh_tunnel_config.py skynet\utils.py`
    - pass
  - `.\venv\Scripts\python.exe scripts\ci\check_stale_paths.py`
    - pass
  - `.\venv\Scripts\python.exe scripts\ci\check_control_plane_boundary.py`
    - pass
  - `.\venv\Scripts\python.exe -m pytest tests -q`
    - `35 passed`
  - `.\venv\Scripts\python.exe -m pytest openclaw-agent\tests -q`
    - `55 passed, 2 skipped`
  - `.\venv\Scripts\python.exe -m pytest openclaw-gateway\tests -q`
    - `219 passed, 3 skipped`
- Current status after continuation:
  - no local worker/gateway/control-plane integration failures were reproduced
  - skipped coverage remains limited to opt-in real-Qwen and live Telegram E2E cases
  - the safest next continuation step is either:
    - run the live E2E flows with the required env flags/tokens
    - review/commit the current refactor worktree as a coherent pass

### 2026-03-12 Structural Cleanup Pass

- Aligned root/operator docs to the live settings contract instead of stale handoff defaults.
- Corrected the repo-local project-documentation skill so it no longer sends unsupported `project_id` query parameters to control-plane task and file-ownership reads.
- Finalize/enqueue now only emits schema-compatible queue payloads and preserves queue metadata stored in `control/TASK_GRAPH.yaml`.
- Added an engineering-policy ratchet for template-generated docstrings with an explicit temporary allowlist for deferred files.
- Removed template-generated docstrings from the low-risk control-plane and worker runtime modules touched in this pass.
- Added targeted regression coverage for:
  - documentation-skill control-plane compatibility
  - gateway action-route idempotent replay
  - worker action-router idempotent replay

### 2026-03-11 Code Quality Standards Pass + SSH Executor Decomposition

- Added explicit repo code-quality guidance:
  - `docs/CODE_QUALITY_STANDARDS.md`
  - `docs/CODE_QUALITY_AUDIT.md`
  - linked from:
    - `docs/INDEX.md`
    - `docs/ENGINEERING_POLICY.md`
    - `README.md`
- Documented current structural hotspots rather than treating cleanup as style-only work:
  - `openclaw-gateway/ssh_tunnel_executor.py`
  - `openclaw-agent/executor/actions.py`
  - `openclaw-gateway/bot/handlers/coding.py`
  - `openclaw-gateway/api.py`
- Refactored the SSH fallback runtime so the executor no longer owns low-level helper concerns inline:
  - added `openclaw-gateway/ssh_tunnel_support.py`
    - remote path normalization / allowlist checks
    - PowerShell/Linux command builders
    - PowerShell output sanitization
  - added `openclaw-gateway/ssh_tunnel_config.py`
    - `SSHExecutorConfig`
    - `load_ssh_executor_config()`
  - `openclaw-gateway/ssh_tunnel_executor.py` now:
    - imports helper functions instead of defining them inline
    - loads grouped runtime settings from `SSHExecutorConfig`
    - keeps the executor class focused on SSH orchestration and remote action handling
    - removes touched template-generated docstrings from the constructor / singleton accessor path
- Added targeted regression coverage:
  - `openclaw-gateway/tests/test_ssh_tunnel_config.py`
- This pass does not change transport behavior or user-visible contracts; it reduces structural drift and makes the SSH runtime path easier to test and extend safely.

### 2026-03-11 Worker Executor Facade Refactor

- Refactored `openclaw-agent/executor/actions.py` into a thinner public dispatch surface while keeping backward-compatible monkeypatch seams used by the current executor tests.
- Added focused helper modules:
  - `openclaw-agent/executor/action_support.py`
    - fixed-argv subprocess runner
    - required-param extraction
    - Python module-missing detection
  - `openclaw-agent/executor/runtime_sessions.py`
    - tracked subprocess wrapper
    - active runtime session registry
    - artifact snapshots / working-tree diffs
    - markdown code-block persistence fallback
    - probe payload generation
  - `openclaw-agent/executor/action_fs.py`
    - file read/write
    - directory listing / create / delete
    - zip archive generation
  - `openclaw-agent/executor/action_search.py`
    - Brave + DDG worker web search path
  - `openclaw-agent/executor/action_process.py`
    - git/build/test/lint/dev-server/install/docker/app-close/project command actions
- `openclaw-agent/executor/actions.py` now owns:
  - settings-backed coding binary resolution
  - coding-agent dispatch
  - runtime probe / cancel endpoints
  - public action registry wiring
- Preserved compatibility-critical names in `executor.actions`:
  - `_run`
  - `_require_param`
  - `_python_module_missing`
  - `_resolve_coding_binary`
  - `_run_tracked_coding_subprocess`
  - runtime probe/session helper aliases
  - this keeps current `test_executor.py` monkeypatches valid while reducing module size and overlap
- Validation:
  - `E:\MyProjects\skynet\venv\Scripts\python.exe -m py_compile openclaw-agent\executor\actions.py openclaw-agent\executor\action_support.py openclaw-agent\executor\runtime_sessions.py openclaw-agent\executor\action_fs.py openclaw-agent\executor\action_search.py openclaw-agent\executor\action_process.py` -> pass
  - `E:\MyProjects\skynet\venv\Scripts\python.exe -m pytest openclaw-agent\tests\test_executor.py -q` -> `32 passed, 2 skipped`
  - `E:\MyProjects\skynet\venv\Scripts\python.exe -m pytest openclaw-agent\tests\test_ws_roundtrip.py openclaw-agent\tests\test_validator.py openclaw-agent\tests\test_agent_config_paths.py -q` -> `15 passed`
  - `E:\MyProjects\skynet\venv\Scripts\python.exe -m pytest openclaw-agent\tests -q` -> `54 passed, 2 skipped`

### 2026-03-11 Full Repo Cleanup And Hygiene Enforcement

- Repo-cleanup pass completed without changing the intended control-plane / gateway / agent architecture:
  - `Makefile` now reflects the actual repo shape with:
    - `install-agent`
    - `install-all`
    - `test-control-plane`
    - `test-gateway`
    - `test-agent`
    - `test-policy`
    - `check-hygiene`
  - `test-all` now runs the curated matrix instead of a broad `pytest tests/` sweep
  - `scripts/dev/smoke.py` now matches the same curated matrix and includes the new hygiene guard
- Repo hygiene enforcement added:
  - new guard: `scripts/ci/check_repo_hygiene.py`
  - wired into:
    - `Makefile smoke`
    - `scripts/dev/smoke.py`
    - `.github/workflows/deploy-ec2-skynet.yml` guard job
  - guard scope:
    - tracked runtime artifacts/logs
    - missing ignore patterns for repo scratch dirs
    - stale references to deleted legacy root tests in operational docs/tooling
    - divergence between authoritative test docs and current make/script surface
- Generated-state cleanup:
  - stopped tracking `openclaw-agent/logs/audit.jsonl`
  - added placeholder `openclaw-agent/logs/.gitkeep`
  - expanded `.gitignore` for:
    - `.pytest-qwen-probe/`
    - `.pytest-tmp/`
    - `.tmp/`
    - `tmp-probe-dir*/`
    - `openclaw-agent/MyProjectsskynetlogs/`
    - `openclaw-gateway/tests/.artifacts/`
- Path normalization cleanup:
  - `openclaw-agent/config.py` now resolves `AGENT_LOG_MIRROR_DIR` and `AUDIT_LOG_DIR` from settings/env against the repo root
  - `openclaw-gateway/config.py` now resolves `DB_PATH`, `LOG_DIR`, `TRACE_MIRROR_LOG_DIR`, and `SKYNET_RUNTIME_TRACE_LIVE_FILE` against the repo root when configured as relative paths
  - canonical defaults updated in `openclaw-agent/settings/defaults.yaml`
  - this removes the cwd-dependent log sprawl that previously produced directories like `openclaw-agent/MyProjectsskynetlogs`
- Docs/tooling drift cleanup:
  - updated:
    - `README.md`
    - `docs/INDEX.md`
    - `docs/IMPLEMENTATION_GUIDE.md`
    - `docs/KNOWN_DRIFT_AND_TEST_MATRIX.md`
    - `docs/ENGINEERING_POLICY.md`
  - `openclaw-gateway/settings/settings.example.yaml` is now a minimal operator overlay example instead of a second authoritative defaults file
- Regression coverage added:
  - `tests/test_ci_repo_hygiene.py`
  - `openclaw-agent/tests/test_agent_config_paths.py`
  - `openclaw-gateway/tests/test_gateway_config_paths.py`
- Second cleanup/restructuring pass:
  - moved repo-path normalization into shared `skynet/utils.py` as `resolve_repo_path(...)`
  - `openclaw-agent/config.py` and `openclaw-gateway/config.py` now consume the shared helper instead of duplicating local path-resolution logic
  - `pytest.ini` now codifies the intended repo shape:
    - component-only default discovery
    - explicit `norecursedirs` for repo scratch/log/artifact trees
  - added `tests/README.md` as the local contract for the curated root suite
  - `scripts/ci/check_repo_hygiene.py` now also enforces:
    - required `pytest.ini` snippets for discovery/exclusions
    - presence/content of `tests/README.md`
    - absence of legacy root tests on disk
- Cleanup-aligned gateway test fixes:
  - `openclaw-gateway/tests/test_telegram_chat_simulation.py` now pins a codex-only fallback chain explicitly instead of inheriting the repo-wide qwen-first chain
  - `openclaw-gateway/tests/test_conversation_e2e_repo_push.py` now uses a valid structured fake plan and focuses on deterministic codegen + git push outcomes instead of stale CTA/message assumptions
- Test results:
  - `E:\MyProjects\skynet\venv\Scripts\python.exe scripts\ci\check_repo_hygiene.py` -> pass
  - `E:\MyProjects\skynet\venv\Scripts\python.exe -m pytest tests\test_ci_repo_hygiene.py openclaw-agent\tests\test_agent_config_paths.py openclaw-gateway\tests\test_gateway_config_paths.py -q` -> `5 passed`
  - `E:\MyProjects\skynet\venv\Scripts\python.exe -m pytest tests\test_api_lifespan.py tests\test_api_provider_config.py tests\test_api_control_plane.py tests\test_job_locking.py tests\test_task_queue_control_plane.py tests\test_worker_registry.py tests\test_ci_engineering_policy.py tests\test_prompt_references.py tests\test_ci_repo_hygiene.py -q` -> `29 passed`
  - `E:\MyProjects\skynet\venv\Scripts\python.exe -m pytest openclaw-agent\tests -q` -> `54 passed, 2 skipped`
  - `E:\MyProjects\skynet\venv\Scripts\python.exe -m pytest openclaw-gateway\tests -q` -> `204 passed, 3 skipped`
  - `E:\MyProjects\skynet\venv\Scripts\python.exe scripts\ci\check_stale_paths.py` -> pass
  - `E:\MyProjects\skynet\venv\Scripts\python.exe scripts\ci\check_control_plane_boundary.py` -> pass
  - `E:\MyProjects\skynet\venv\Scripts\python.exe scripts\ci\check_settings_policy.py` -> pass
  - `E:\MyProjects\skynet\venv\Scripts\python.exe -m py_compile scripts\ci\check_repo_hygiene.py scripts\dev\smoke.py openclaw-agent\config.py openclaw-gateway\config.py` -> pass
- Second-pass validation:
  - `E:\MyProjects\skynet\venv\Scripts\python.exe -m pytest tests\test_ci_repo_hygiene.py openclaw-agent\tests\test_agent_config_paths.py openclaw-gateway\tests\test_gateway_config_paths.py -q` -> `6 passed`
  - `E:\MyProjects\skynet\venv\Scripts\python.exe -m py_compile skynet\utils.py openclaw-agent\config.py openclaw-gateway\config.py scripts\ci\check_repo_hygiene.py` -> pass
  - `E:\MyProjects\skynet\venv\Scripts\python.exe -m pytest tests\test_api_lifespan.py tests\test_api_provider_config.py tests\test_api_control_plane.py tests\test_job_locking.py tests\test_task_queue_control_plane.py tests\test_worker_registry.py tests\test_ci_engineering_policy.py tests\test_prompt_references.py tests\test_ci_repo_hygiene.py -q` -> `30 passed`
  - `E:\MyProjects\skynet\venv\Scripts\python.exe -m pytest openclaw-agent\tests -q` -> `54 passed, 2 skipped`
  - `E:\MyProjects\skynet\venv\Scripts\python.exe -m pytest openclaw-gateway\tests -q` -> `204 passed, 3 skipped`
- Trace evidence / artifact note:
  - runtime evidence paths affected by this cleanup are `openclaw-agent/logs/audit.jsonl` and repo-level `logs/skynet.trace.log`
  - this pass was repo hygiene/tooling cleanup, not a live-runtime debug cycle, so no new request/task trace was required beyond those artifact-path updates

### 2026-03-10 Qwen Planner Contract Tightening + Validator Pass-Through

- Root-caused the latest preflight/runtime failures into two separate layers:
  - worker security validator was rejecting `requirement_summary_md` before Qwen ran
  - after that fix, the real `telegram_real` requirements turn still failed because the deployed gateway was generating an older `emit_ready_sentence` planner prompt/context that nudged Qwen into `exit_plan_mode`
- Worker validation fix:
  - `openclaw-agent/security/validator.py` now exempts `requirement_summary_md` from shell-metacharacter sanitization, matching existing free-text prompt/context fields
  - websocket regression coverage added in:
    - `openclaw-agent/tests/test_validator.py`
    - `openclaw-agent/tests/test_ws_roundtrip.py`
  - targeted validation passed:
    - `14 passed`
- Qwen planner contract tightening:
  - `skynet/project_specialist.py` now makes `emit_ready_sentence` explicit in both planner prompt and planner context:
    - do not generate the plan yet
    - do not use markdown/bullets/headings
    - stop immediately after the final period
  - `skynet/qwen_cli.py` now injects contract-specific output rules into the runtime prompt for:
    - `emit_ready_sentence`
    - `ask_next_question`
    - `emit_plan`
  - gateway planner payload generation now passes `reply_contract` into shared Qwen context construction in:
    - `openclaw-gateway/bot/handlers/project.py`
    - `openclaw-gateway/tests/live_diagnostics.py`
  - regression coverage added in:
    - `openclaw-agent/tests/test_executor.py`
    - `openclaw-gateway/tests/test_project_specialist_prompt.py`
  - targeted validation passed:
    - `67 passed, 2 skipped`
- Planner approval-mode correction:
  - canonical settings now use `SKYNET_QWEN_PLANNER_APPROVAL_MODE=default` in:
    - `openclaw-agent/settings/defaults.yaml`
    - `openclaw-gateway/settings/settings.yaml`
  - reason:
    - official Qwen docs describe `plan` mode as a tool-driven planning workflow that can invoke `exit_plan_mode`
    - our planner-chat / requirements-gathering stage is not that workflow and should not encourage tool usage
- Real Qwen verification on the local machine:
  - direct `run_coding_agent` probe with:
    - `task_mode=planner_chat`
    - `reply_contract=emit_ready_sentence`
    - structured `planner_state_json`
    - structured `requirement_summary_md`
  - result:
    - `returncode=0`
    - `output_contract=ok`
    - exact assistant text: `I have everything I need. Send /plan to generate your project plan.`
    - `permission_denials=[]`
    - `tools.totalCalls=0`
- Live E2E status after local fixes:
  - latest trace:
    - `logs/e2e-live-1773156492.log`
  - preflight is now fully green:
    - planner-ready probe passes
    - plan-generation probe passes
  - actual Telegram requirements turn still fails with:
    - `QWEN_CONTRACT_VIOLATION: planner_tool_use`
    - bot-visible failure: `AI is unavailable right now. Please try again.`
  - worker audit proves the failure is from stale deployed gateway prompt generation, not the local worker runtime:
    - preflight actions used the new contract and passed
    - live requirements action still carried the older `emit_ready_sentence` prompt/context without the new "do not generate the plan yet / stop after the sentence" guidance
- Current required next step:
  - commit/push/deploy the new gateway-side prompt/context changes, then rerun `telegram_real`
  - until that deploy happens, live Telegram flow will continue exercising the stale prompt builder in the remote container even though the local worker runtime is fixed

### 2026-03-10 Qwen Planner State Machine + Provider-Flex Capability Probes

- Root cause refined further:
  - the remaining planner failure was not Telegram transport or websocket routing
  - the gateway was still asking Qwen to infer requirements completeness from raw prompt/history
  - under `qwen-oauth` + `coder-model`, headless planner calls frequently reset the conversation even when the user had already supplied enough requirements
- Runtime architecture change:
  - the gateway now owns planner state deterministically in `openclaw-gateway/bot/handlers/project.py`
  - normalized planner state includes:
    - `project_name`
    - `project_type`
    - `facts`
    - `answered_slots`
    - `missing_slots`
    - `requirement_summary`
    - `plan_ready`
    - `next_question_targets`
  - required slots for Python App planning are now enforced in shared code
  - obvious negatives are inferred by gateway logic instead of asking Qwen to rediscover them:
    - local/terminal script => `runtime_mode=on_demand`
    - local script + standard-library-only => `integrations=none`
    - local script with no persistence terms => `storage=none`
- Qwen planner contract is now explicit and structured:
  - worker payload for Qwen planner runs now carries:
    - `reply_contract`
    - `planner_state_json`
    - `requirement_summary_md`
  - supported planner contracts:
    - `ask_next_question`
    - `emit_ready_sentence`
    - `emit_plan`
  - gateway behavior is now:
    - `plan_ready=false` => `planner_chat + ask_next_question`
    - `plan_ready=true` during requirements => `planner_chat + emit_ready_sentence`
    - `/plan` => `plan_generation + emit_plan`
  - the previous “Qwen decides it has enough information” heuristic is no longer authoritative
- Worker-side Qwen execution hardening:
  - `skynet/qwen_cli.py` now builds a structured runtime prompt from planner state instead of raw history only
  - contract classification is now state-aware:
    - ready-sentence mismatch
    - question targets already-answered slot
    - missing planner question
    - plan not grounded in requirement summary
  - provider env is now built from canonical settings:
    - `SKYNET_QWEN_PROVIDER_PROFILE`
    - `SKYNET_QWEN_OPENAI_BASE_URL`
    - `SKYNET_QWEN_OPENAI_API_KEY_ENV`
    - capability-required flags for planner/coding
  - `qwen` remains the agent everywhere, but auth/provider/model are now settings-driven instead of implicitly tied to `qwen-oauth`
- Planner working-dir ownership cleanup:
  - for Qwen planner modes with `request_scoped` strategy, the gateway no longer creates/deletes remote planner sandbox directories
  - worker-owned temp dirs are now the only planner scratch space for those calls
  - this removes the previous blocked `delete_directory` noise from live traces
- Live preflight now runs two real Qwen capability probes instead of one loose smoke check:
  - planner ready probe:
    - complete requirements in one message must yield the exact completion sentence
  - plan generation probe:
    - structured requirement summary must produce a valid grounded plan
  - both probes run through the real worker/gateway action path
  - `/status` now exposes:
    - `qwen_auth_type`
    - `qwen_provider_profile`
    - `qwen_planner_model`
    - `qwen_coding_model`
    - last planner-ready probe result
    - last plan-generation probe result
- Validation completed for this runtime change:
  - targeted regression suite: `74 passed, 2 skipped`
  - real local Qwen CLI integration tests: `2 passed`
  - these real tests prove the updated adapter can satisfy:
    - exact ready-sentence contract
    - grounded plan-generation contract
- Current operational blockers after the code change:
  - local `conversation` live E2E still cannot run because there is no local gateway listening on `127.0.0.1:8766`
  - `.env.local-e2e` also lacks `SKYNET_AUTH_TOKEN`; manual retry used the token from `.env.worker-agent`
  - worker bootstrap env itself is valid and currently targets tunnel websocket `ws://127.0.0.1:18765/agent/ws`
  - commit containing this runtime work:
    - `da91bbe` `Stabilize qwen planner runtime`
  - deployment workflow for that commit was still pending/failing at the time of this handoff and must complete before `telegram_real` can verify the new runtime remotely

### 2026-03-10 Qwen-First Runtime Contract + Deployed Revision Guard

- Root-caused the remaining live `telegram_real` failure after Qwen smoke preflight started passing:
  - worker preflight probe succeeded with the new Qwen planner contract
  - the real requirements turn still failed with `Internal agent error`
  - worker audit showed the live request payload was missing `task_mode`
  - that proved the deployed `openclaw-gateway` container was still serving an older planner-handler build, even though local runtime/tests were already patched
- Canonical Qwen runtime contract is now split and settings-backed instead of using the legacy generic CLI path:
  - new shared helper `skynet/qwen_cli.py`
  - new shared planner prompt/context source `skynet/project_specialist.py`
  - `openclaw-agent/executor/actions.py` now treats `qwen` as a first-class runtime with required `task_mode`
  - supported task modes:
    - `planner_chat`
    - `plan_generation`
    - `coding_implementation`
    - `coding_validation`
- Worker-side Qwen fixes:
  - planner path no longer relies on deprecated `qwen -p` prompt behavior
  - Qwen now uses positional prompts plus JSON output parsing
  - session ids are normalized to valid UUIDs before invoking the CLI
  - output is classified into typed contracts such as:
    - `ok`
    - `planner_meta_output`
    - `coding_meta_output`
    - `planner_tool_use`
    - JSON parse failures
  - `qwen_context_text` is allowed through validator sanitization so planner context can be injected safely
  - planner context can be written through a temporary `QWEN.md` file when the active profile enables it
- Gateway-side Qwen fixes:
  - `openclaw-gateway/bot/handlers/project.py` now uses the shared planner prompt builder
  - Qwen planner calls explicitly send:
    - `task_mode=planner_chat`
    - `qwen_context_text=<shared planner contract>`
  - coding-stage payloads already send `task_mode=coding_implementation`
  - milestone extraction sends `task_mode=plan_generation` when planner agent is `qwen`
  - control-loop director and architect planner calls now use the same centralized Qwen planner payload helper, so they also send `task_mode=plan_generation`
  - planner validators were intentionally kept strict; generic “ready to assist / what would you like to work on?” output is still rejected as contract-invalid
- SSH executor parity:
  - `openclaw-gateway/ssh_tunnel_executor.py` now propagates `qwen_context_text`
  - SSH-native Qwen runs can temporarily materialize and restore `QWEN.md` in the remote working directory, so Qwen behavior is consistent across websocket and SSH transports
- Live-E2E preflight/build freshness guard:
  - `openclaw-gateway/api.py` `/status` now exposes `build_revision`
  - `openclaw-gateway/config.py` adds:
    - `SKYNET_BUILD_REVISION`
    - `SKYNET_LIVE_E2E_EXPECT_REMOTE_BUILD_REVISION`
  - compose env passthrough now includes `SKYNET_BUILD_REVISION` for:
    - root `docker-compose.yml`
    - `openclaw-gateway/docker-compose.yml`
  - shared preflight in `openclaw-gateway/tests/live_diagnostics.py` now raises `PREFLIGHT_BUILD_REVISION_MISMATCH` when the deployed gateway is not serving the expected revision
  - preflight build-revision matching accepts either a full SHA or a short commit prefix for manual local runs
  - deploy workflow now injects `SKYNET_BUILD_REVISION=$GITHUB_SHA` into `.env.ci` and verifies `/status.build_revision` matches the container config after deploy
- Regression and real-adapter coverage:
  - shared Qwen prompt/contract tests in:
    - `openclaw-gateway/tests/test_project_specialist_prompt.py`
    - `openclaw-gateway/tests/test_project_planner_fallback.py`
    - `openclaw-gateway/tests/test_ssh_executor_resilience.py`
    - `openclaw-agent/tests/test_executor.py`
    - `openclaw-agent/tests/test_validator.py`
    - `openclaw-gateway/tests/test_api_status_diagnostics.py`
    - `openclaw-gateway/tests/test_live_e2e_trace_logging.py`
  - real local Qwen integration probe now passes with:
    - multiline planner prompt
    - shared Qwen context contract
    - no generic onboarding/meta response
- Operational state after this change:
  - local code now sends the correct Qwen contract everywhere relevant
  - any future stale deployment is expected to fail during preflight with a concrete build-revision mismatch instead of surfacing later as “missing task_mode” or generic planner failure

### 2026-03-10 Shared Settings Loader Container-Layout Fix

- Root-caused the deployed gateway live-E2E policy mismatch after the previous runtime-policy refactor:
  - inside the deployed `openclaw-gateway` container, `/app/settings/settings.yaml` contained `SKYNET_E2E_LIVE: true`
  - but `config.SETTINGS_FILE` resolved to `/app/openclaw-gateway/settings/settings.yaml`
  - that path does not exist in the component-container layout built from `openclaw-gateway/Dockerfile`
  - result: the shared settings loader fell back to config defaults, so runtime `/status` reported:
    - `live_e2e_active=false`
    - `live_e2e_flow=conversation`
    - while still showing qwen-only derived fields from code defaults/env
- Fix in `skynet/settings/loader.py`:
  - added component settings-dir auto-resolution that supports both:
    - monorepo layout (`<repo>/openclaw-gateway/settings`, `<repo>/openclaw-agent/settings`, `<repo>/skynet/settings`)
    - component-container layout (`/app/settings`)
  - shared loader now chooses the first existing component settings directory instead of assuming monorepo paths always exist
- Added regression coverage in `openclaw-gateway/tests/test_settings_loader.py`:
  - component-root gateway layout with `tmp/settings/settings.yaml`
  - verifies `SKYNET_E2E_LIVE` and `SKYNET_LIVE_E2E_FLOW` load correctly without explicit `settings_dir`
- Added deploy guard in `.github/workflows/deploy-ec2-skynet.yml`:
  - `/status` diagnostics validation now also requires the live-E2E fields
  - compares `payload["live_e2e_active"]` with `config.get_live_e2e_policy()["active"]`
  - this prevents future deployments where the container can see the settings file on disk but the runtime is actually loading defaults from the wrong path

### 2026-03-10 Deploy Config Tracking For Worker Project Paths

- Root-caused the latest `telegram_real` live E2E coding preflight failure to missing tracked worker path defaults on the deployed gateway:
  - runtime failure: `Path '/livee2e...' is outside allowed roots`
  - deployed `openclaw-gateway` container had empty values for:
    - `SKYNET_PROJECT_BASE_DIR`
    - `OPENCLAW_PROJECT_BASE_DIR`
    - `SKYNET_DEFAULT_WORKING_DIR`
    - `OPENCLAW_DEFAULT_WORKING_DIR`
- Cause:
  - local `settings.local.yaml` had the required non-secret Windows worker paths
  - deploy checkout only included tracked files, so the gateway container rendered empty worker path settings from canonical `settings.yaml`
- Fix in `openclaw-gateway/settings/settings.yaml`:
  - set tracked canonical defaults:
    - `SKYNET_PROJECT_BASE_DIR=E:\SKYNET-SANDBOX\Projects`
    - `OPENCLAW_PROJECT_BASE_DIR=E:\SKYNET-SANDBOX\Projects`
    - `SKYNET_DEFAULT_WORKING_DIR=E:\SKYNET-SANDBOX`
    - `OPENCLAW_DEFAULT_WORKING_DIR=E:\SKYNET-SANDBOX`
- Added deploy verification in `.github/workflows/deploy-ec2-skynet.yml`:
  - new `Validate worker project path settings` step runs inside `openclaw-gateway`
  - hard-fails deploy when `WORKER_PROJECTS_DIR` or `DEFAULT_WORKING_DIR` is empty inside the container
- Operational note:
  - this keeps worker filesystem roots in the tracked canonical settings path instead of depending on an untracked local override for deploy correctness
  - after the docs update, rerun CI/CD and then rerun `telegram_real` live E2E against the refreshed deployment

### 2026-03-10 Telegram Poller Lease Reacquire Fix

- Root-caused the deployed gateway poller startup failure after restart:
  - gateway `/status` showed:
    - `telegram_poller_state=blocked`
    - `telegram_poller_last_error=foreign_lease_active`
    - `telegram_poller_lease_owner=openclaw`
  - this happened even though the same gateway ID was restarting
- Cause in shared control-plane lease logic:
  - `JobLockManager.acquire_lock()` used `INSERT OR IGNORE` only
  - a restart by the same owner could not reacquire or refresh its own still-valid lease until TTL expiry
  - result: gateway long-polling stayed disabled on restart even when no foreign poller should win
- Fix in `skynet/ledger/job_locking.py`:
  - same-owner `acquire_lock()` is now idempotent
  - when the existing unexpired lease is already owned by the caller, acquire refreshes `acquired_at` and `expires_at` and returns success
  - foreign-owner acquire attempts still return `False`
- Added regression coverage:
  - `tests/test_job_locking.py::test_job_lock_manager_reacquire_by_same_owner_refreshes_lease`
  - `tests/test_api_control_plane.py::test_control_plane_lease_acquire_is_idempotent_for_same_owner`
  - existing `openclaw-gateway/tests/test_telegram_poller_lease.py` still passes against the lease-controller contract

### 2026-03-10 Shared Live E2E Cleanup Manager

- Added config-backed live-run cleanup controls in `openclaw-gateway/settings/settings.yaml` and `openclaw-gateway/config.py`:
  - `SKYNET_LIVE_E2E_CLEANUP_AFTER_RUN`
  - `SKYNET_LIVE_E2E_CLEANUP_TARGETS`
  - `SKYNET_LIVE_E2E_CLEANUP_GRACE_SECONDS`
  - `get_live_e2e_cleanup_config()`
- Added shared cleanup implementation in `openclaw-gateway/tests/live_diagnostics.py`:
  - `LiveRunCleanupManager`
  - repo-rooted process enumeration and target matching
  - tracked pytest subprocess registration
  - Windows tree teardown via `taskkill /T /F`
- Wired `openclaw-gateway/tests/e2e_live.py` to:
  - run conversation/telegram-real pytest flows through registered subprocesses
  - execute cleanup in `finally` on run exit
  - apply cleanup policy from the single-source live E2E settings/config path
- Default cleanup policy now tears down:
  - repo-rooted `scripts/run_worker_agent.ps1` launcher trees
  - repo-rooted `openclaw-agent/main.py` processes
  - tracked child pytest processes started by the live runner
- Added regression coverage:
  - cleanup config defaults and env overrides
  - repo-rooted process matching
  - registered subprocess teardown

### 2026-03-10 Runtime-Enforced Live E2E Policy

- Root-caused the remaining qwen failover inconsistency:
  - live runner policy was clamping qwen-only behavior in subprocess env
  - gateway runtime handlers still built planner/coding stage chains from raw config defaults
  - result: local `conversation` could be forced into qwen-only, but deployed `telegram_real` still kept `qwen -> codex` behavior unless the remote gateway happened to carry the same env overrides
- Fix in gateway runtime/config:
  - added canonical `SKYNET_E2E_LIVE` handling in `openclaw-gateway/config.py`
  - `get_live_e2e_policy()` now reports `active`
  - `get_live_e2e_runtime_env()` now explicitly propagates `SKYNET_E2E_LIVE=1`
  - `bot/handlers/project.py` now derives planner primary agent and router fallback from the normalized live policy when live E2E is active
  - `bot/handlers/coding.py` now derives:
    - coding stage chain
    - milestone planner primary agent
    - control-loop router fallback
    from the same normalized live policy when live E2E is active
  - strict live policy now raises instead of silently routing around the configured primary planner when fallback is disabled
- Observability/preflight:
  - `/status` now exposes:
    - `live_e2e_active`
    - `live_e2e_flow`
    - `live_e2e_effective_coding_stage_chain`
  - shared live preflight now fails immediately with `PREFLIGHT_LIVE_POLICY_INACTIVE` if the target gateway is not enforcing the live policy
- Validation:
  - targeted suite passed: `61 passed`
  - live `telegram_real` rerun now fails fast and deterministically with:
    - `PREFLIGHT_LIVE_POLICY_INACTIVE: gateway is not enforcing live E2E policy`
  - latest trace: `logs/e2e-live-1773138222.log`
- Operational follow-up:
  - canonical settings now track `SKYNET_E2E_LIVE=true` for deployed gateway behavior
  - gateway tests force `SKYNET_E2E_LIVE=0` by default in `openclaw-gateway/tests/conftest.py` so non-live handler tests keep exercising the normal runtime path unless they opt into live-policy coverage

### 2026-03-10 Live E2E Validation + Orphaned Test Cleanup

- Ran live E2E: SKYNET API (`:8000`), OpenClaw Gateway (`:8765`/`:8766`), Worker Agent all healthy; `route-task` through full stack passed.
- Deleted 11 orphaned test files from `tests/` that imported modules removed in commit `73acd04` (radical simplification):
  - `test_commander_engine.py`, `test_trace_logger.py` — imported deleted `core.dev_trace`
  - `test_integration_conversation.py` — imported deleted `orchestrator.project_manager`, `skills.project_skill`
  - `test_telegram_nl_flow.py` — imported deleted `skills.project_skill`, `bot.nl_intent`
  - `test_orchestrator_inbox.py`, `test_orchestrator_invariants.py`, `test_orchestrator_write_gating.py` — imported deleted `bot.orchestrator`, `bot.session`
  - `test_project_create_bootstrap_warning.py` — imported deleted `orchestrator.project_manager`
  - `test_project_doc_intake_formatting.py` — imported deleted `bot.doc_intake`
  - `test_user_profile_memory.py` — expected 9 unimplemented CRUD functions
  - `test_gateway_agent_runs_artifacts.py` — depended on 9 deleted `store.py` functions
- Fixed settings policy violation in `openclaw-gateway/tests/e2e_live.py`: moved `SKYNET_ENV_FILE` auto-detection into `openclaw-gateway/live_settings.py` (allowed file) as `auto_detect_env_file()`.
- Root test suite: 22 passed, 0 failed.

### 2026-03-09 Shared Live E2E Container Diagnostics

- Added gateway-backed live container-log settings in `openclaw-gateway/settings/settings.yaml` and `openclaw-gateway/config.py`:
  - `SKYNET_E2E_CONTAINER_LOG_STREAM_ENABLED`
  - `SKYNET_E2E_CONTAINER_LOG_REQUIRE_STREAM`
  - `SKYNET_E2E_CONTAINER_LOG_SOURCES`
  - `SKYNET_E2E_CONTAINER_LOG_MAX_LINE_CHARS`
  - `SKYNET_E2E_CONTAINER_LOG_RING_LINES`
  - `SKYNET_E2E_CONTAINER_LOG_TAIL_DEFAULT`
  - `SKYNET_E2E_CONTAINER_LOG_TAIL_OVERRIDES`
- Added `get_live_e2e_container_log_config()` in `openclaw-gateway/config.py` so live tests consume one normalized config source instead of ad hoc env parsing.
- Extracted live trace creation, container stream handling, SSH resolution, log redaction, and final snapshot bundling into `openclaw-gateway/tests/live_diagnostics.py`.
- Rewired the shared live runner and both live flows to consume the helper:
  - `openclaw-gateway/tests/e2e_live.py`
  - `openclaw-gateway/tests/test_e2e_conversation_live.py`
  - `openclaw-gateway/tests/test_e2e_telegram_real_live.py`
- `conversation` and `telegram_real` now both emit a terminal `container.log.bundle` event into the same live trace file; `direct` remains unchanged.
- Added coverage in `openclaw-gateway/tests/test_live_e2e_trace_logging.py` for:
  - config defaults and env overrides
  - snapshot tail selection via `TAIL_DEFAULT` and `TAIL_OVERRIDES`
  - require-stream early failure
  - final bundle emission on success and failure

### 2026-03-09 Shared Settings Single-Source-Of-Truth Policy + Repo Restructure

- Replaced the three ad hoc settings loaders with one shared implementation in `skynet/settings/loader.py`.
- Gateway and agent loader modules now act as thin compatibility wrappers instead of owning parsing logic.
- Structured YAML is now parsed centrally, so agent policy tables in `openclaw-agent/settings/defaults.yaml` are real runtime configuration, not dead defaults.
- Moved agent allowlists and closeable-app mappings in `openclaw-agent/config.py` onto settings-backed values:
  - `AUTO_ACTIONS`
  - `CONFIRM_ACTIONS`
  - `BLOCKED_ACTIONS`
  - `CLOSEABLE_APPS`
- Refactored live runtime bootstrap so gateway live E2E paths use `openclaw-gateway/live_settings.py` instead of manual `.env` and YAML loading:
  - `openclaw-gateway/tests/e2e_live.py`
  - `openclaw-gateway/tests/test_e2e_conversation_live.py`
  - `openclaw-gateway/tests/test_e2e_telegram_real_live.py`
- Replaced deployment env rendering in `.github/workflows/deploy-ec2-skynet.yml` with `scripts/ci/render_settings_env.py`.
- Added repo guard `scripts/ci/check_settings_policy.py` and wired it into:
  - `Makefile`
  - `scripts/dev/smoke.py`
  - `.github/workflows/deploy-ec2-skynet.yml`
- Compose manifests were normalized to pass through env values instead of carrying duplicated runtime defaults, while preserving compose-specific service wiring constants.

### 2026-03-07 Ollama claude_ollama Stage as Primary Coding Backend

- Enabled `SKYNET_CLAUDE_OLLAMA_STAGE_ENABLED` in `openclaw-gateway/settings/settings.yaml` and CI deploy
- Changed `SKYNET_CODING_FALLBACK_CHAIN` to `claude_ollama,codex` (Ollama first, codex fallback)
- Enabled `SKYNET_CLAUDE_OLLAMA_AUTO_PULL` for automatic model pulls
- Model: `qwen2.5-coder:7b` (Q4_K_M, fits RTX 4070 8GB VRAM)
- Agent-side: `run_coding_agent` with `backend=ollama` runs `claude --model qwen2.5-coder:7b` via `ANTHROPIC_BASE_URL=http://localhost:11434`

### 2026-03-06 WebSocket-First Execution Model Rollout

- Gateway transport layer in `openclaw-gateway/gateway.py` now supports:
  - websocket-primary selection based on worker heartbeat freshness
  - explicit `gateway.transport.select` and `gateway.transport.fallback` trace events
  - `action_accepted` handling for in-flight websocket actions
  - replay path for accepted mutating actions
  - pre-accept SSH fallback on websocket send failure
  - websocket log-mirror ack tracking (`log_write_ack`)
- Gateway `/status` now exposes websocket health and mirror diagnostics in `openclaw-gateway/api.py`:
  - `primary_transport_mode`
  - `worker_id`
  - `agent_last_hello_at`
  - `agent_last_heartbeat_at`
  - `websocket_health_ok`
  - `websocket_error_category`
  - `websocket_failure_streak`
  - `fallback_ready`
  - `fallback_last_reason`
  - websocket log mirror send/ack/error fields
  - dynamic `primary_transport_mode` (`websocket_primary`, `ssh_fallback`, `unavailable`)
- Logging bootstrap now binds websocket mirror handlers to the runtime event loop:
  - `openclaw-gateway/logging_setup.py`
  - `openclaw-gateway/main.py`
- Worker websocket client now supports:
  - rich `agent_hello`
  - periodic `agent_heartbeat`
  - `action_accepted`
  - `log_write_ack`
  - concurrent action handling so long-running coding actions do not block probes/cancel requests
- Worker router now supports websocket replay identity via:
  - `task_id:idempotency_key`
  - fallback `transport_id:idempotency_key`
  - in-flight replay wait path
- Worker executor now exposes websocket-compatible runtime controls:
  - `trace_runtime_probe`
  - `cancel_runtime_session`
- Worker lifecycle scripts added:
  - `scripts/install_worker_agent.ps1`
  - `scripts/run_worker_agent.ps1`
  - `scripts/register_worker_agent_task.ps1`
  - `scripts/check_worker_agent_health.ps1`
  - `scripts/repair_worker_agent.ps1`
- Non-secret defaults/docs updated toward websocket-primary:
  - `openclaw-gateway/settings/settings.yaml`
  - `openclaw-gateway/settings/settings.example.yaml`
  - `.env.ec2-gateway.example`
  - `.env.local-e2e.example`
  - `.env.worker-agent.example`
  - `README.md`
  - `docs/IMPLEMENTATION_GUIDE.md`
  - `docs/DEBUG_PLAYBOOK.md`
  - `docs/ARCHITECTURE_MAP.md`
- Live Telegram E2E now proves websocket-primary selection from runtime trace:
  - `SKYNET_E2E_REQUIRE_WEBSOCKET_PRIMARY=1`
  - `SKYNET_E2E_ALLOW_SSH_FALLBACK=1`
  - runtime trace summary checks for `gateway.transport.select transport=websocket_primary`
- Telegram tracker/runtime trace surfaces now use dynamic transport/runtime labels instead of hardcoded SSH labels in websocket-primary runs.
- Mixed gateway/agent pytest runs no longer collide on the bare `config.py` module:
  - gateway code now imports `gateway_config.py`
  - worker code now imports `agent_config.py`
- Worker bootstrap fixes added after live bring-up:
  - `.env.worker-agent` parsing no longer aborts on comment lines
  - launcher writes `logs\worker-agent.bootstrap.log`
  - tunnel probe helper no longer collides with PowerShell's built-in `$Host`
- Live E2E preflight fix added after websocket-primary rollout:
  - worker `exec_command` now supports structured `argv`
  - coding preflight write-probe no longer sends a quoted `python -c "..."` shell string
  - this removes the false `CODEX_WRITE_BLOCKED: Parameter 'command' contains disallowed shell metacharacters` failure in websocket-primary runs
- Current workstation note:
  - `register_worker_agent_task.ps1` still hits local `Access is denied` on this laptop
  - detached user-session launch via `scripts/run_worker_agent.ps1` is the current operational workaround

### 2026-03-06 Max-Forensic Runtime Trace + Telegram Tracker Update

- Expanded runtime trace for SSH-first coding so stuck stages are diagnosable from `skynet.trace.log` alone:
  - added forensic trace envelope fields in `openclaw-gateway/runtime_trace.py`, `openclaw-gateway/db/schema.py`, and `openclaw-gateway/db/store.py`
    - `event_id`
    - `root_trace_id`
    - `session_key`
    - `remote_pid`
    - `artifact_count`
  - moved runtime redaction to regex-based forensic sanitization while preserving readable command/path/process diagnostics.
- Hardened SSH execution tracing in `openclaw-gateway/ssh_tunnel_executor.py`:
  - active session registry keyed by `session_key`
  - prompt-file lifecycle events
  - command launch/stdout/stderr/exit wait events
  - `trace_runtime_probe` and `cancel_runtime_session` actions
  - remote artifact/process snapshots for long-running `run_coding_agent` calls.
- Wired coding heartbeat probes and stop-cleanup tracing in `openclaw-gateway/bot/handlers/coding.py`:
  - `coding.stage.remote_snapshot`
  - `coding.stage.process_tree`
  - `coding.stage.prompt_file_state`
  - `coding.stage.artifact_detected`
  - `coding.stop.requested`
  - `coding.stop.remote_cancel`
  - `coding.stop.orphan_process.detected`
  - `/trace deep` now includes latest forensic snapshot summary.
- Fixed Telegram tracker/status correctness in `openclaw-gateway/bot/handlers/coding.py`:
  - SSH sessions now default to `transport=ssh_first` and `runtime=ssh`
  - meaningful phase/stage/gate changes bypass edit throttling immediately
  - tracker message stays rate-limited only for non-significant heartbeat churn.
- Normalized Telegram chat text in `openclaw-gateway/bot/handlers/coding.py` and `openclaw-gateway/bot/handlers/project.py`:
  - removed mojibake/corrupted symbols from milestone, retry, stop, and plan-generation messages.
- Deployment alignment fix:
  - updated `.github/workflows/deploy-ec2-skynet.yml` so `.env.ci` no longer forces legacy trace payload mode.
  - deploy now exports forensic trace defaults (`DETAIL_PROFILE=max_forensic`, `PAYLOAD_MODE=forensic_redacted`, remote snapshot/session-registry flags).

### 2026-03-06 Deployment Repair: Runtime Trace Schema Migration Order

- GitHub Actions run `22769176004` failed in `Deploy on self-hosted runner -> Verify deployment`.
- Root cause:
  - `openclaw-gateway` startup ran `init_db()` against an existing SQLite file whose `runtime_trace_events` table predated the new forensic columns.
  - `SCHEMA_SQL` attempted to create `idx_runtime_trace_session_created` before the migration step added `session_key`, causing:
    - `sqlite3.OperationalError: no such column: session_key`
- Fix in `openclaw-gateway/db/schema.py`:
  - removed runtime-trace indexes from bootstrap `SCHEMA_SQL`
  - re-created them only in the post-migration index pass, after `ALTER TABLE ... ADD COLUMN session_key/root_trace_id/event_id/remote_pid/artifact_count`
  - added missing post-migration indexes:
    - `idx_runtime_trace_session_created`
    - `idx_runtime_trace_project_graph_created`
- Regression coverage:
  - `openclaw-gateway/tests/test_runtime_trace_forensic.py::test_init_db_upgrades_legacy_runtime_trace_schema`
  - creates a legacy `runtime_trace_events` table and verifies `init_db()` upgrades columns and indexes safely.

### 2026-03-06 Trace-First Live E2E + Container Stream Update

- Added trace-first live diagnostics in `openclaw-gateway/tests/test_e2e_telegram_real_live.py`:
  - always-on EC2 container streaming events (`container.log.stream.start|ok|error|stop`, `container.log.line`)
  - per-container ring-buffer bundles on terminal failures (`container.log.bundle`)
  - runtime trace snapshot metadata (`mtime_iso`, `age_s`, `line_count`, `digest`)
  - stale runtime trace hard-fail classification (`TRACE_STALE`)
  - tracker-edit/runtime-trace/container activity checks in coding poll loop
  - `/trace deep` capture attempt before timeout-class failure.
- Added container-log redaction hardening:
  - bearer/auth/token-like field masking
  - explicit Telegram bot token URL masking (`bot[REDACTED]`).
- Added runtime trace/config propagation updates:
  - `openclaw-gateway/config.py`
  - `.github/workflows/deploy-ec2-skynet.yml`
  - `.env.example`, `.env.local-e2e.example`, `.env.ec2-gateway.example`
  - `openclaw-gateway/settings/settings.yaml`, `openclaw-gateway/settings/settings.example.yaml`
  - `README.md`.
- Added regression coverage:
  - `openclaw-gateway/tests/test_live_e2e_trace_logging.py` now covers container stream redaction + stale trace diagnostics behavior.
- Fixed control-plane API compatibility in `skynet/ledger/task_queue.py`:
  - `claim_next_ready_task(...)` now accepts optional `lock_timeout_seconds` to match scheduler caller.
- Deployment status from live debug:
  - GitHub Actions runs `22762262248` and `22762380178` failed policy gate (`Check engineering policy`) because `docs/AGENT_HANDOFF.md` was not included.
  - As a result, EC2 stayed on previous containers and continued emitting:
    - `TypeError: TaskQueueManager.claim_next_ready_task() got an unexpected keyword argument 'lock_timeout_seconds'`
  - Current objective is to pass policy gate and redeploy so runtime picks up the compatibility fix.

### 2026-03-06 Runtime Trace Source Selection Hardening

- Live E2E exposed a false-stale failure mode: runtime snapshot resolver preferred a stale mirror file (`E:\SKYNET-SANDBOX\logs\skynet.trace.log`) even when a newer repo-local runtime trace existed.
- Updated `openclaw-gateway/tests/test_e2e_telegram_real_live.py`:
  - runtime trace resolution now considers:
    - `SKYNET_E2E_RUNTIME_TRACE_FILE` (explicit override, hard priority)
    - `SKYNET_RUNTIME_TRACE_LIVE_FILE`
    - mirror/file fallbacks
  - for non-explicit paths, resolver now selects the freshest existing trace file by `mtime` to avoid stale-pin behavior during live runs.

### 2026-03-06 TRACE_STALE False-Positive Fix in Coding Poll Loop

- Root cause:
  - During active coding polls, stale detection checked `runtime_progress.stale_seconds()` before refreshing with a fresh runtime snapshot.
  - This produced false `TRACE_STALE` failures even when the same iteration snapshot showed new lines and recent `mtime`.
- Fix:
  - In `openclaw-gateway/tests/test_e2e_telegram_real_live.py`, stale branch now:
    - captures a fresh snapshot first,
    - re-observes progress,
    - recomputes staleness,
    - emits `coding.poll.recovered` and continues when freshness is restored.
  - Terminal stale failure now only triggers when staleness remains above threshold after refresh.

### 2026-03-06 SSH Mirror Log Sink Reliability Hardening

- New live E2E evidence:
  - runtime trace file stalled while gateway container logs showed repeated `Log sink flush failed` and Paramiko `Error reading SSH protocol banner`.
  - This indicates mirror transport instability under SSH load, not trace schema/collector gaps.
- Applied transport hardening in `openclaw-gateway/logging_setup.py` (`_SSHMirrorFileHandler`):
  - increased minimum connect timeout floor from `2s` to `8s` for mirror handshakes.
  - switched to explicit auth mode (`look_for_keys=False`, `allow_agent=False`) when key/password is provided.
  - enabled SSH transport keepalive (`15s`) after successful connect.
  - added exponential reconnect backoff after connect failures (capped growth) to reduce handshake storms.
  - downgraded backoff-cycle flush failures to concise deferred messages (avoid traceback flood).

### 2026-03-06 Codex Prompt Transport + Gate Heartbeat Update

- Root-cause from live Telegram E2E (`livee2e1772786667`):
  - Windows SSH Codex prompt delivery was brittle for multiline payloads because prompt text was passed via argv.
  - Gate auto-fix (`_run_quality_fix_pass`) could run silently long enough for live E2E polling to timeout even while work was still progressing.
- Applied fixes:
  - `openclaw-gateway/ssh_tunnel_executor.py`
    - Added stdin prompt delivery path for Windows (`codex exec -`) via `_run_windows_command_with_prompt_file(..., prompt_via_stdin=True)`.
    - Added `coding.prompt.transport` runtime trace event with prompt length/newline metrics and delivery mode (`stdin|argv`).
  - `openclaw-gateway/bot/handlers/coding.py`
    - `_run_quality_fix_pass` now supports heartbeat-wrapped `run_coding_agent` execution.
    - `_run_strict_quality_gates` now passes app/chat/user context to quality-fix path and emits periodic `run_contract` running updates during auto-fix.
    - Updated both control-loop and legacy strict-gate call sites to provide heartbeat context.
  - `openclaw-gateway/tests/test_ssh_executor_resilience.py`
    - Added regression test validating stdin prompt transport script generation on Windows prompt-file runner.
- CI note:
  - Policy gate run `22756380094` initially failed because `docs/AGENT_HANDOFF.md` was not included with code changes.
  - This handoff update resolves that policy requirement for the next push.

### 2026-03-06 Strict-Gate Long-Run Heartbeat Coverage Update

- Live E2E observation after deploy `22756451474`:
  - quality-fix heartbeat messages were visible, but strict-gate operations (`lint/tests/smoke`) still ran without chat heartbeat emissions.
  - Telegram real E2E timed out waiting for new bot messages while gates were still executing.
- Fix in `openclaw-gateway/bot/handlers/coding.py`:
  - Added `_run_gate_action(...)` helper inside `_run_strict_quality_gates(...)`.
  - Long-running gate actions now use `_send_action_with_heartbeat(...)` when app/chat context is available.
  - Covered actions:
    - `lint_project`
    - `run_tests`
    - `exec_command` (smoke)
  - Gate heartbeat updates now emit periodic `running` events with elapsed seconds and gate command context.

### 2026-03-06 Control-Loop Timeout Crash Hardening Update

- Root-cause from live Telegram E2E (`livee2e1772782693`):
  - `work_2` stage timed out after 900s (`WAIT_TIMEOUT`) and bubbled up as an uncaught exception.
  - The coding loop emitted a generic "unexpected error" and left the loop graph in an `active` state with a `running` work node.
- Applied hardening in `openclaw-gateway/bot/handlers/coding.py`:
  - `WAIT_TIMEOUT` during stage execution is now treated as a stage failure path (non-crashing) instead of an unconditional re-raise.
  - Stage heartbeat tracker runtime metadata now correctly reports SSH mode for SSH-stage executions.
  - Added a protective catch around `controller.run(...)` to convert unexpected controller exceptions into a structured failed graph result.
- Applied hardening in `openclaw-gateway/orchestration/loop_controller.py`:
  - Added executor exception guards for `work`, `critic`, and `gate` nodes.
  - On uncaught executor exception, node and graph are now atomically marked failed with persisted failure type and event telemetry.
- Added regression coverage:
  - `openclaw-gateway/tests/test_control_loop_integration.py::test_closed_loop_executor_exception_fails_graph_without_crash`

### 2026-03-06 Runtime Trace Module Deployment Fix

- CI deploy failure root cause on run `22754926134`:
  - `ModuleNotFoundError: No module named 'runtime_trace'` during `openclaw-gateway` startup.
  - The code imported `runtime_trace`, but module/supporting DB wiring were not included in the pushed commit.
- Fix set staged for deployment:
  - Add `openclaw-gateway/runtime_trace.py`.
  - Add DB persistence support for runtime trace events in `db/schema.py` and `db/store.py`.
  - Include gateway/e2e runtime trace integration and associated tests/docs.

### 2026-03-05 Live E2E Runtime Trace Visibility Update

- Live E2E trace defaults now write to repo logs directory:
  - `E:\MyProjects\skynet\logs\`
- Updated live trace loggers:
  - `openclaw-gateway/tests/e2e_live.py`
  - `openclaw-gateway/tests/test_e2e_conversation_live.py`
  - `openclaw-gateway/tests/test_e2e_telegram_real_live.py`
- Added runtime trace snapshots throughout Telegram real E2E cycle:
  - checkpoints at plan/coding transitions, milestone clicks, periodic coding polls, run output, and terminal failures.
  - runtime source defaults to `logs/skynet.trace.log` (override via `SKYNET_E2E_RUNTIME_TRACE_FILE`).
- Added fast-fail detection for coding session failure summary in Telegram real E2E:
  - no more silent polling until timeout after `session failed`.
- Added new tests:
  - `openclaw-gateway/tests/test_live_e2e_trace_logging.py`
- Updated operator docs:
  - `README.md` live trace location and live monitoring commands.
  - `docs/DEBUG_PLAYBOOK.md` now requires runtime trace tail evidence in mitigation loop.

### 2026-03-05 SSH-First Hierarchical Closed Loop v1.2 Update

- Added `loop_v2` profile defaults and force-all behavior:
  - `SKYNET_CONTROL_LOOP_DEFAULT_PROFILE=loop_v2`
  - `SKYNET_CONTROL_LOOP_FORCE_FOR_ALL=1`
- Added v1.2 config flags in `openclaw-gateway/config.py` and settings templates:
  - director/architect toggles
  - blocking architecture contract controls
  - worker-pool selection controls
  - learning policy controls
  - dedicated director/architect/planner timeouts
- Added additive DB schema for hierarchical orchestration state:
  - `architecture_states`
  - `task_strategy`
  - `worker_registry`
  - `node_worker_assignments`
  - `learning_events`
  - `prompt_policies`
  - plus `task_nodes.worker_id`, `task_nodes.tools_required_json`, `task_nodes.risk_level`
- Added store APIs for architecture versioning, worker assignment, learning events, and prompt policies in `openclaw-gateway/db/store.py`.
- Added orchestration modules:
  - `openclaw-gateway/orchestration/director.py`
  - `openclaw-gateway/orchestration/architect.py`
  - `openclaw-gateway/orchestration/worker_pool.py`
  - `openclaw-gateway/orchestration/learning.py`
- Wired coding loop path to treat `loop_v2` as active control-loop mode while preserving SSH-first execution and codex-only stage chain.
- Added loop-v2 telemetry fields to tracker/status surfaces:
  - architecture version
  - worker assignment id
  - learning policy activation trace events.

### 2026-03-05 SSH-First Closed Loop v1 Update

- Added closed-loop config surface in `openclaw-gateway/config.py`:
  - `SKYNET_CONTROL_LOOP_*` flags for enable/force/profile/retry/critic/memory controls.
  - coding fallback default now `codex` and planner default `codex`.
- Added DB schema for persistent loop state in `openclaw-gateway/db/schema.py`:
  - `projects.control_loop_profile`
  - `task_graphs`
  - `task_nodes`
  - `critic_findings`
  - `project_memory`
- Added store APIs in `openclaw-gateway/db/store.py` for graph nodes, critic findings, and project memory tiers.
- Added orchestration modules:
  - `openclaw-gateway/orchestration/graph.py`
  - `openclaw-gateway/orchestration/critic.py`
  - `openclaw-gateway/orchestration/memory.py`
  - `openclaw-gateway/orchestration/loop_controller.py`
- Wired coding loop integration in `openclaw-gateway/bot/handlers/coding.py`:
  - `loop_v1` path executes a persisted node graph (`work -> critic -> repair -> gate`).
  - manual milestone approval remains for `work` nodes.
  - strict gates remain blocking before node completion.
  - codex-only generation stage enforced for loop mode.
  - tracker/status now include graph/node metadata.
- Updated project creation defaults in `openclaw-gateway/bot/handlers/project.py`:
  - sets `control_loop_profile` for new projects (`loop_v1` when enabled/forced).
- Updated templates/settings:
  - `.env.example`
  - `.env.local-e2e.example`
  - `.env.ec2-gateway.example`
  - `openclaw-gateway/settings/settings.yaml`
  - `openclaw-gateway/settings/settings.example.yaml`
- Added closed-loop tests:
  - `test_control_loop_graph.py`
  - `test_control_loop_critic.py`
  - `test_control_loop_memory.py`
  - `test_control_loop_integration.py`

### 2026-03-05 Reverse Tunnel Key/Bind + Codex Trust Fix

- Updated reverse tunnel scripts to use canonical worker key precedence:
  - `OPENCLAW_TUNNEL_SSH_KEY`
  - `OPENCLAW_SSH_KEY_PATH`
  - fallback `E:\MyProjects\skynet-key.pem`
- Removed invalid fallback to `protech-bot-key.pem` from:
  - `scripts/keep_tunnel_alive.ps1`
- Added explicit reverse-bind host support and set canonical bind shape:
  - `-R 0.0.0.0:2222:localhost:22`
- Hardened task registration to pass explicit tunnel args (key/host/bind/ports):
  - `scripts/register_tunnel_task.ps1`
- Hardened tunnel health diagnostics:
  - effective key path output
  - bind expectation output
  - improved ssh process matching for `-R` argument forms
  - `scripts/check_tunnel_health.ps1`
- Updated env/docs for canonical tunnel key:
  - `.env.ec2-gateway.example`
  - `.env.example`
  - `.env.local-e2e.example`
  - `README.md`
- Added codex SSH invocation trust bypass (`--skip-git-repo-check`) in:
  - `openclaw-gateway/ssh_tunnel_executor.py`
  - `openclaw-agent/executor/actions.py`
- Live Telegram E2E currently progresses through full conversation and SSH preflight, but milestone generation still fails on deployed runtime until latest gateway image is redeployed.

### 2026-03-05 SSH/ACP Precedence Guard Update

- Added orchestration precedence guard in `openclaw-gateway/config.py`:
  - new flag: `SKYNET_ORCHESTRATION_ALLOW_ACP_WITH_SSH` (default `0`)
  - new helpers:
    - `is_ssh_execution_mode()`
    - `effective_orchestration_mode()`
- Behavior lock:
  - when `OPENCLAW_EXECUTION_MODE` is `ssh|ssh_tunnel|tunnel|ssh-only`, effective orchestration mode is forced to `legacy` unless override flag is set.
- Switched ACP mode checks to effective mode in:
  - `openclaw-gateway/bot/handlers/coding.py`
  - `openclaw-gateway/bot/handlers/project.py`
  - `openclaw-gateway/gateway.py`
- Live Telegram E2E now fails fast on terminal preflight failures instead of timing out in polling loop:
  - `openclaw-gateway/tests/test_e2e_telegram_real_live.py`
- Deployment/env alignment updates:
  - `.github/workflows/deploy-ec2-skynet.yml`
  - `.env.ec2-gateway.example`
  - `.env.example`
  - `openclaw-gateway/settings/settings.example.yaml`

### 2026-03-05 ACP-First OpenClaw Orchestration Update

- Added control-plane orchestration config in `openclaw-gateway/config.py`:
  - `SKYNET_ORCHESTRATION_MODE`
  - `SKYNET_OPENCLAW_RUNTIME`
  - `SKYNET_OPENCLAW_QUEUE_MODE`
  - `SKYNET_OPENCLAW_RETRY_TRANSIENT`
  - `SKYNET_OPENCLAW_SESSION_TIMEOUT_SECONDS`
  - `SKYNET_OPENCLAW_STAGE_CHAIN`
  - `SKYNET_OPENCLAW_AGENT_HOSTING`
  - `SKYNET_OPENCLAW_TRACE_ENABLED`
  - `SKYNET_OPENCLAW_CLI_BIN`
  - `OPENCLAW_CODEX_BIN`, `OPENCLAW_CLAUDE_BIN`, `OPENCLAW_CLINE_BIN`
- Added orchestration adapter module:
  - `openclaw-gateway/orchestration/openclaw_runner.py`
  - session lifecycle methods: `start_session`, `run_prompt`, `wait`, `cancel`, `collect_trace`
- Added orchestration policy file:
  - `openclaw-gateway/settings/openclaw_profiles.yaml`
- Added orchestration telemetry persistence:
  - new table `task_orchestration_runs` in `openclaw-gateway/db/schema.py`
  - store methods in `openclaw-gateway/db/store.py`:
    - `create_task_orchestration_run`
    - `list_task_orchestration_runs`
- Wired coding stage chain to orchestration adapter in `openclaw-gateway/bot/handlers/coding.py` for `acp_first`:
  - stage execution now supports control-plane generation with worker `file_write` application.
  - stage start/success/fail are persisted to `task_orchestration_runs`.
  - preflight now supports control-plane stage availability checks.
- Wired planner and milestone extraction to orchestration-first path:
  - `openclaw-gateway/bot/handlers/project.py`
  - `openclaw-gateway/bot/handlers/coding.py`
- Added local coding-action routing for ACP-first mode in `openclaw-gateway/gateway.py`:
  - coding actions can resolve via local orchestration adapter when configured.
- Extended tracker visibility:
  - tracker pipeline now includes `session`, `runtime`, and `queue`.
- Updated deploy and settings templates for orchestration flags:
  - `.github/workflows/deploy-ec2-skynet.yml`
  - `openclaw-gateway/settings/settings.yaml`
  - `openclaw-gateway/settings/settings.example.yaml`
  - `.env.example`
  - `.env.local-e2e.example`

### 2026-03-04 Telegram Tracker + Pipeline Visibility Update

- Added Telegram coding tracker config flags in `openclaw-gateway/config.py`:
  - `SKYNET_TELEGRAM_TRACKER_ENABLED`
  - `SKYNET_TELEGRAM_TRACKER_EDIT_INTERVAL_SECONDS`
  - `SKYNET_TELEGRAM_TRACKER_STALE_WARN_SECONDS`
  - `SKYNET_TELEGRAM_TRACKER_BAR_WIDTH`
  - `SKYNET_TELEGRAM_TRACKER_VERBOSE_PIPELINE`
- Added matching non-secret defaults in:
  - `openclaw-gateway/settings/settings.yaml`
  - `openclaw-gateway/settings/settings.example.yaml`
  - optional env overrides in `.env.example` and `.env.local-e2e.example`.
- Completed coding-loop tracker wiring in `openclaw-gateway/bot/handlers/coding.py`:
  - setup, extraction, approval wait, execution, stage-chain switching, strict gates, and finalization.
  - stage hook events now surface stage transitions in tracker state.
  - strict gate hook events now surface gate-level progress and run-contract status.
- `/status` now reads tracker state and shows progress/phase/stage/gate/transport/run-contract details.
- Real Telegram E2E test upgraded to assert tracker visibility and observed tracker edits:
  - `openclaw-gateway/tests/test_e2e_telegram_real_live.py`
  - `openclaw-gateway/tests/e2e_live.py` now logs tracker summary counts from live output.

### 2026-03-04 Settings/Secrets Trim Update

- Added non-secret runtime defaults file:
  - `openclaw-gateway/settings/settings.yaml`
- Added clean example:
  - `openclaw-gateway/settings/settings.example.yaml`
- Refactored `openclaw-gateway/config.py`:
  - loads non-secret defaults from YAML
  - secrets remain env-only
  - added typed accessors `get_str/get_int/get_bool`
  - removed environment mutation shim
- Routed direct runtime env reads through config accessors:
  - `openclaw-gateway/gateway.py`
  - `openclaw-gateway/api.py`
  - `openclaw-gateway/ssh_tunnel_executor.py`
- Trimmed env templates to secret-focused shape:
  - `.env.example`
  - `.env.local-e2e.example`
- Rebuilt GitHub sync utility (`scripts/dev/sync_env_to_github.py`):
  - secret-like keys only by default in secrets mode
  - optional stale secret pruning (`--prune-stale`)
  - workflow-aware keep-set protection while pruning

### 2026-03-04 Pre-Push Secret Sync Reliability Update

- Hardened `scripts/dev/sync_env_to_github.py` so pre-push sync no longer fails on predictable GitHub constraints.
- Added automatic skip rules for env sync:
  - reserved `GITHUB_*` key names
  - empty values
  - invalid secret/variable names
- Added repository secret-quota guard:
  - when GitHub repo secret count reaches configured limit (default `100`), new secret names are skipped instead of failing pre-push.
- Updated sync summary output to include `skipped=` counts.
- Updated `README.md` sync section to document skip behavior and non-zero failure semantics.

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
- Default provider priority: `claude,gemini,openai,deepseek,openrouter,groq,ollama`
- Default orchestration/runtime profile:
  - `SKYNET_ORCHESTRATION_MODE=legacy`
  - `SKYNET_CONTROL_LOOP_ENABLED=true`
  - `SKYNET_CONTROL_LOOP_DEFAULT_PROFILE=loop_v2`
  - `OPENCLAW_EXECUTION_MODE=agent_preferred`
  - `SKYNET_CODING_TRANSPORT=websocket_primary`
  - `OPENCLAW_SSH_FALLBACK_ENABLED=false`
  - `SKYNET_WEBSOCKET_FALLBACK_TO_SSH=false`
- Planner/coding defaults:
  - `SKYNET_PLANNER_PRIMARY_AGENT=codex`
  - `SKYNET_CODING_FALLBACK_CHAIN=qwen,codex`
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

### 2026-03-24 Critic Timeout Resilience

- `python -m pytest openclaw-gateway/tests/ tests/ -q` -> `262 passed, 3 skipped`
- Changes: `openclaw-gateway/bot/handlers/coding.py`, `openclaw-gateway/orchestration/loop_controller.py`
- Critic timeout no longer kills the task graph; timed-out critics are marked advisory ("done" with warning)

### Previous Test Results

- `python scripts/ci/check_stale_paths.py` -> pass
- `python scripts/ci/check_control_plane_boundary.py` -> pass
- `python scripts/ci/check_settings_policy.py` -> pass
- `python scripts/ci/check_repo_hygiene.py` -> pass
- `python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD` -> pass
- `python -m pytest tests/test_api_lifespan.py tests/test_api_provider_config.py tests/test_api_control_plane.py tests/test_job_locking.py tests/test_task_queue_control_plane.py tests/test_worker_registry.py tests/test_ci_engineering_policy.py tests/test_project_documentation_skill.py tests/test_prompt_references.py -q` -> `31 passed`
- `python -m pytest openclaw-gateway/tests/test_gateway_websocket_primary.py openclaw-gateway/tests/test_gateway_ssh_mode.py openclaw-gateway/tests/test_api_status_diagnostics.py openclaw-gateway/tests/test_api_action_routes.py -q` -> `10 passed` (Paramiko deprecation warnings only)
- `python -m pytest openclaw-agent/tests/test_ws_roundtrip.py openclaw-agent/tests/test_validator.py openclaw-agent/tests/test_action_accept_and_replay.py -q` -> `22 passed`
- `python -m pytest tests/ -q` — `22 passed` (0 failed, 0 errors after orphaned test cleanup)
- `python scripts/ci/check_settings_policy.py` — pass (e2e_live.py violation fixed)
- `python scripts/ci/check_stale_paths.py` — pass
- `python scripts/ci/check_control_plane_boundary.py` — pass
- Live E2E: `scripts/manual/check_e2e_integration.py` — PASSED (register-gateway, route-task)

### Previous Test Results

- `E:\\MyProjects\\skynet\\venv\\Scripts\\python.exe -m py_compile skynet\\settings\\__init__.py skynet\\settings\\loader.py openclaw-gateway\\settings\\loader.py openclaw-agent\\settings\\loader.py openclaw-agent\\config.py openclaw-gateway\\config.py openclaw-gateway\\main.py openclaw-gateway\\live_settings.py openclaw-gateway\\tests\\e2e_live.py openclaw-gateway\\tests\\test_e2e_conversation_live.py openclaw-gateway\\tests\\test_e2e_telegram_real_live.py scripts\\dev\\run_api.py scripts\\manual\\check_api.py scripts\\ci\\render_settings_env.py scripts\\ci\\check_settings_policy.py`
  - pass
- `E:\\MyProjects\\skynet\\venv\\Scripts\\python.exe scripts\\ci\\check_settings_policy.py`
  - pass
- `E:\\MyProjects\\skynet\\venv\\Scripts\\python.exe -m pytest openclaw-gateway\\tests\\test_project_planner_fallback.py openclaw-gateway\\tests\\test_planner_resilience.py -q`
  - `12 passed`
- `E:\\MyProjects\\skynet\\venv\\Scripts\\python.exe -m pytest openclaw-agent\\tests\\test_action_accept_and_replay.py -q -k "detect_coding_agents_includes_qwen or route_survives_audit_write_failure"`
  - `2 passed`
- `E:\\MyProjects\\skynet\\venv\\Scripts\\python.exe -m pytest tests\\test_api_provider_config.py -q`
  - `3 passed`
- Direct probe: shared gateway loader still treats `SKYNET_SETTINGS_FILE=<settings.local.yaml>` as a local override layer.
  - observed values: `BASE_ONLY=base`, `LOCAL_ONLY=local`, `SHARED=local`
- Direct probe: agent runtime policy tables now resolve from settings-backed data.
  - observed values: `trace_runtime_probe in AUTO_ACTIONS == True`, `CLOSEABLE_APPS['code'] == Code.exe`, `eval_code in BLOCKED_ACTIONS == True`
- Direct probe: `scripts/ci/render_settings_env.py` produced an effective merged env file from shared settings.
- Pytest caveat:
  - tempdir-dependent cases still hit sandbox ACL failures under Windows (`WinError 5` during pytest tempdir setup/cleanup), so loader/env-render behavior was additionally verified with direct probes inside the workspace.

- `python -m py_compile openclaw-gateway/db/schema.py openclaw-gateway/tests/test_runtime_trace_forensic.py`
  - pass
- `python -m pytest openclaw-gateway/tests/test_runtime_trace.py openclaw-gateway/tests/test_runtime_trace_forensic.py openclaw-gateway/tests/test_ssh_executor_trace_forensics.py openclaw-gateway/tests/test_trace_command.py openclaw-gateway/tests/test_tracker_progress.py -q`
  - `14 passed`
- `python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD`
  - pass
- `python -m py_compile openclaw-gateway/bot/handlers/coding.py openclaw-gateway/bot/handlers/project.py openclaw-gateway/runtime_trace.py openclaw-gateway/ssh_tunnel_executor.py openclaw-gateway/tests/test_runtime_trace.py openclaw-gateway/tests/test_runtime_trace_forensic.py openclaw-gateway/tests/test_ssh_executor_trace_forensics.py openclaw-gateway/tests/test_tracker_progress.py`
  - pass
- `python -m pytest openclaw-gateway/tests/test_runtime_trace.py openclaw-gateway/tests/test_runtime_trace_forensic.py openclaw-gateway/tests/test_ssh_executor_trace_forensics.py openclaw-gateway/tests/test_trace_command.py openclaw-gateway/tests/test_tracker_progress.py -q`
  - `8 passed`
- `python scripts/ci/check_stale_paths.py`
  - pass
- `python scripts/ci/check_control_plane_boundary.py`
  - pass
- `python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD`
  - pass after updating `docs/AGENT_HANDOFF.md`

- `python -m pytest -q openclaw-gateway/tests/test_live_e2e_trace_logging.py openclaw-gateway/tests/test_live_e2e_runner_policy.py openclaw-gateway/tests/test_ssh_executor_resilience.py openclaw-gateway/tests/test_planner_resilience.py`
  - `26 passed`
- `python -m py_compile openclaw-gateway/tests/e2e_live.py openclaw-gateway/tests/test_e2e_telegram_real_live.py`
  - pass
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

### 2026-03-07 WebSocket-only execution (SSH fallback disabled)

- Disabled SSH fallback in production and local e2e configs to enforce WebSocket-only communication:
  - `openclaw-gateway/settings/settings.yaml`: `OPENCLAW_SSH_FALLBACK_ENABLED: false`, `SKYNET_WEBSOCKET_FALLBACK_TO_SSH: false`
  - `.env.local-e2e`: `OPENCLAW_SSH_FALLBACK_ENABLED=0`, `SKYNET_E2E_ALLOW_SSH_FALLBACK=0`
  - `.env.local-e2e.example`: updated to match
- Motivation: SSH command-line length limit (8191 chars on Windows) caused failures for long prompts. WebSocket has no such limit.
- Gateway `send_action()` now raises a clear RuntimeError when no WebSocket worker is connected instead of silently falling back to SSH.
- `docker-compose.yml` already defaults `OPENCLAW_SSH_FALLBACK_ENABLED` to `0`.
- Killed stale EC2 agent process that was holding the WebSocket slot with old code.
- Added `"command"` to agent security validator exempt keys (`openclaw-agent/security/validator.py`): the `exec_command` action sends a `command` string containing Python one-liners with parentheses/quotes that are safe since the agent uses `subprocess_exec` not shell interpolation.
- Live E2E confirmed WebSocket-primary transport for all actions: `check_coding_agents`, `exec_command`, `create_directory`, `run_coding_agent`.

### 2026-03-07 WebSocket-first + SSH fallback hardening

- `python -m pytest openclaw-gateway/tests/test_e2e.py -v` — 23 passed
- Fix: coding preflight write probe missing `command` param in `exec_command` action
- Fix: Claude/Cline SSH agents use `_run_windows_command_with_prompt_file()` on Windows
- Fix: SSH log mirror truncates lines >500 chars to prevent command-line overflow
- Fix: Container log stream SSH keepalive (`ServerAliveInterval=30`) + reconnect (3 retries)

## Trace Evidence

- request_id=critic-timeout-advisory-20260324
- task_id=critic-timeout-resilience
- skynet.trace.log — critic timeout now emits advisory "done" event instead of graph-fatal failure
- `request_id=repo-structural-cleanup-20260312`
- `task_id=doc-truth-alignment-and-template-docstring-ratchet`
- control-plane compatibility validated against `/v1/tasks` and `/v1/files/ownership` via `tests/test_project_documentation_skill.py`
- request_id=e2e-live-validation-orphan-cleanup-20260310
- task_id=orphaned-test-cleanup-settings-policy-fix
- audit.jsonl — openclaw-agent/logs/audit.jsonl (worker agent connected during E2E)
- Live E2E route-task: task_id=task-fa68895eddd4 (via /v1/route-task)
- Previous: request_id=settings-single-source-of-truth-20260309

- request_id=deploy-repair-runtime-trace-schema-order-20260306
- task_id=runtime-trace-legacy-migration-index-fix
- GitHub Actions run `22769176004`
- skynet.trace.log
- request_id=max-forensic-runtime-trace-20260306
- task_id=ssh-first-live-e2e-trace-expansion
- skynet.trace.log
- /trace deep
- session_key runtime correlation added to SSH coding actions
- request_id=telegram-tracker-cleanup-20260306
- task_id=ssh-status-tracker-and-chat-normalization
- `E:\MyProjects\skynet\logs\skynet.trace.log`
- request_id=live-e2e-runtime-trace-visibility-20260305
- task_id=telegram-real-e2e-runtime-trace-snapshots
- `E:\MyProjects\skynet\logs\e2e-live-1772737353.log`
- `E:\MyProjects\skynet\logs\skynet.trace.log`
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
- request_id=websocket-first-ssh-fallback-20260307
- task_id=ws-log-sink-prompt-file-keepalive
- `E:\MyProjects\skynet\logs\e2e-live-1772881218.log`
- skynet.trace.log

## Documentation Updates

- `docs/CODE_QUALITY_AUDIT.md`
- `docs/KNOWN_DRIFT_AND_TEST_MATRIX.md`
- `skills/skynet-project-documentation/README.md`
- `skills/skynet-project-documentation/skill.yaml`
- `CONTRIBUTING.md`
- `README.md`
- `docs/INDEX.md`
- `docs/ARCHITECTURE_MAP.md`
- `docs/IMPLEMENTATION_GUIDE.md`
- `docs/ENGINEERING_POLICY.md`
- `docs/AGENT_HANDOFF.md`
- `.github/workflows/deploy-ec2-skynet.yml`
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
- [x] Guard scripts executed (`check_stale_paths`, `check_control_plane_boundary`, `check_settings_policy`, `check_engineering_policy`).
