# Manual Checks

This folder contains manual integration checks that depend on running services.

- `check_api.py`: probes SKYNET API endpoints on `http://localhost:8000`
- `check_e2e_integration.py`: verifies OpenClaw Gateway to SKYNET flow
- `check_skynet_delegate.py`: validates the OpenClaw `skynet_delegate` skill

These are intentionally outside `tests/` because they are not deterministic unit tests.
