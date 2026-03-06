# Engineering Policy

Repository policy for documentation quality, testing evidence, and tracing evidence.

## Documentation Requirements

Documentation updates are mandatory when a change affects:

- behavior visible to users/operators
- API contracts or endpoint semantics
- task scheduling/locking or execution routing
- environment variables, deployment, or runbooks

Required updates by default:

- `docs/AGENT_HANDOFF.md` for any code-path change in `skynet/`, `openclaw-gateway/`, `openclaw-agent/`, or `scripts/`
- relevant docs in this pack (`ARCHITECTURE_MAP`, `IMPLEMENTATION_GUIDE`, `DEBUG_PLAYBOOK`, `KNOWN_DRIFT_AND_TEST_MATRIX`) if behavior or operational guidance changed

## Testing Requirements

Any code change must include one of:

- test evidence: exact command(s) run and result summary
- explicit `NoTestJustification` in handoff, with reason and risk statement

Default required suites for platform changes:

- control-plane curated tests
- gateway test suite
- agent test suite
- CI guards (`check_stale_paths`, `check_control_plane_boundary`, `check_engineering_policy`)

## Tracing Requirements

Behavior changes and bugfixes require trace evidence in handoff.

Accepted evidence markers include at least one of:

- `request_id`
- `task_id`
- `claim_token`
- `audit.jsonl`
- `skynet.trace.log`
- `/v1/events`

Trace evidence must include enough context to correlate logs to the changed behavior.

Runtime trace completeness is mandatory for E2E-path changes:

- Every terminal `fail` event must include a `debug.bundle`.
- Required runtime event envelope fields:
  - `ts`, `level`, `event`, `trace_id`, `span_id`, `parent_span_id`, `status`
  - flow context (`flow`, `project_id`, `task_id`, `graph_id`, `node_key`, `node_type`)
  - execution context (`phase`, `stage`, `gate`, `worker_id`, `transport`, `runtime_mode`)
  - transport/action context (`action_name`, `command_hash`, `working_dir`)
- Missing required event fields or missing `debug.bundle` is a policy failure.

## Live E2E Debug Policy Requirements

When the issue is a live `telegram_real` failure, `docs/DEBUG_PLAYBOOK.md` mandatory loop applies.

Required before declaring fix complete:

- failure classified to one primary category
- bug explanation documented (`symptom -> root cause -> impact`)
- mitigation plan written before edits
- debug-cycle commit created and pushed successfully
- rerun evidence showing end-to-end pass
- artifact verification (`.py` files + valid `skynet_run.json` + run exit `0`)
- trace evidence for both failed run and passing run

## Merge and Handoff Evidence

`docs/AGENT_HANDOFF.md` must contain non-empty sections:

- `## Test Results`
- `## Trace Evidence`
- `## Documentation Updates`
- `## Policy Checklist`

For code-path changes, handoff file must be updated in the same change set.

## Policy Checklist

Copy/paste for handoff entries:

```markdown
## Policy Checklist

- [ ] Documentation updated for behavior/interface/ops changes.
- [ ] Tests run, commands listed, and outcomes recorded.
- [ ] Trace evidence recorded for behavior changes (request/task/trace markers).
- [ ] For live `telegram_real` failures, followed `DEBUG_PLAYBOOK` loop with mitigation plan + failed/pass trace evidence.
- [ ] For live `telegram_real` failures, commit and push succeeded before rerunning live E2E.
- [ ] `NoTestJustification` provided if tests were not run.
- [ ] Guard scripts executed (`check_stale_paths`, `check_control_plane_boundary`, `check_engineering_policy`).
```
