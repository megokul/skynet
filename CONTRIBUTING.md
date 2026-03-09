# Contributing

## Scope

This repository enforces one boundary:
- OpenClaw executes tasks.
- SKYNET orchestrates OpenClaw gateways.

Source of truth: `docs/SKYNET_OPENCLAW_CONTRACT.md`.

Configuration source of truth:
- runtime defaults belong in component settings files
- the shared loader in `skynet/settings/loader.py` is the only settings parser/env bootstrap path
- do not add duplicate defaults to scripts, tests, Docker Compose, or workflows

## Local Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

Set at least one gateway URL in `.env`:
- `OPENCLAW_GATEWAY_URL`
- or `OPENCLAW_GATEWAY_URLS`

## Run

```bash
python scripts/dev/run_api.py
```

## Tests and Checks

```bash
python -m pytest tests -q
python scripts/ci/check_stale_paths.py
python scripts/ci/check_control_plane_boundary.py
python scripts/dev/smoke.py
```

## Pull Request Rules

1. Keep changes inside control-plane scope for `skynet/`.
2. Do not add runtime execution/tool/memory/session logic back into `skynet/`.
3. Update docs when API surface changes:
   - `README.md`
   - `AGENT_GUIDE.md`
   - `docs/SKYNET_OPENCLAW_CONTRACT.md` (if contract changes)
4. Keep runtime settings changes centralized in the shared settings loader + component settings files.
