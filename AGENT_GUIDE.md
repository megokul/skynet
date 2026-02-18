# SKYNET Agent Guide

## Contract

Source of truth: `docs/SKYNET_OPENCLAW_CONTRACT.md`.

Hard rule:
- `skynet/` is control-plane orchestration only.
- OpenClaw owns runtime execution, tools, memory, sessions, and channels.

## What SKYNET May Do

- Register gateways/workers
- Route tasks to OpenClaw gateway APIs
- Track topology and health metadata
- Expose control-plane API endpoints

## What SKYNET Must Not Do

- Execute shell/tools/scripts
- Manage agent runtime internals
- Run memory/session systems for agents
- Call model providers for agent execution flow

## Current API

- `POST /v1/register-gateway`
- `POST /v1/register-worker`
- `POST /v1/route-task`
- `GET /v1/system-state`
- `GET /v1/health`

## Validation

Use:
- `python scripts/ci/check_control_plane_boundary.py`
- `make test`
