# SKYNET Documentation Index

This index is the primary entrypoint for coding agents and engineers working in this repository.

## Reading Order

1. Read `docs/ARCHITECTURE_MAP.md` to understand runtime boundaries and ownership.
2. Read `docs/IMPLEMENTATION_GUIDE.md` for module-level implementation details.
3. Read `docs/DEBUG_PLAYBOOK.md` before debugging or incident response.
4. Read `docs/KNOWN_DRIFT_AND_TEST_MATRIX.md` to avoid stale assumptions.
5. Read `docs/ENGINEERING_POLICY.md` before writing code, tests, or docs.

## Authoritative Sources

- System contract: `docs/SKYNET_OPENCLAW_CONTRACT.md`
- Agent operating guidance: `AGENT_GUIDE.md`
- Current handoff state and evidence: `docs/AGENT_HANDOFF.md`

## Quick Commands

```bash
make test
make smoke
python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD
```
