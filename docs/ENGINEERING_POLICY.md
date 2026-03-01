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
- [ ] `NoTestJustification` provided if tests were not run.
- [ ] Guard scripts executed (`check_stale_paths`, `check_control_plane_boundary`, `check_engineering_policy`).
```
