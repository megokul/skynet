# SKYNET Documentation Index

This index is the primary entrypoint for coding agents and engineers working in this repository.

## Reading Order

1. Read `docs/ARCHITECTURE_MAP.md` to understand runtime boundaries and ownership.
2. Read `docs/IMPLEMENTATION_GUIDE.md` for module-level implementation details.
3. Read `docs/DEBUG_PLAYBOOK.md` before debugging or incident response.
4. Read `docs/KNOWN_DRIFT_AND_TEST_MATRIX.md` to avoid stale assumptions.
5. Read `docs/ENGINEERING_POLICY.md` before writing code, tests, or docs.
6. Read `docs/CODE_QUALITY_STANDARDS.md` before refactoring runtime modules.
7. Read `docs/CODE_QUALITY_AUDIT.md` to target structural cleanup where it matters.
8. Treat `skynet/settings/loader.py` plus the three component settings files as the only runtime settings source of truth.

For live Telegram E2E failures, the `DEBUG_PLAYBOOK` loop is mandatory policy, not optional guidance.  
Push-gate rule: rerun live E2E only after debug-cycle commit + successful push.

## Authoritative Sources

- System contract: `docs/SKYNET_OPENCLAW_CONTRACT.md`
- Agent operating guidance: `AGENT_GUIDE.md`
- Current handoff state and evidence: `docs/AGENT_HANDOFF.md`
- Curated root-test guidance: `tests/README.md`

## Quick Commands

```bash
make test-control-plane
make test-gateway
make test-agent
make test-policy
make test-all
make smoke
python scripts/ci/check_repo_hygiene.py
python scripts/ci/check_settings_policy.py
python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD
```
