# Docker Deployment

## Purpose

This repo can run:
- SKYNET control plane API (`skynet-api`)
- OpenClaw gateway (optional, separate execution plane)

## Quick Start

```bash
cp .env.example .env
docker-compose build
docker-compose up -d
```

## Required Environment

For SKYNET control plane:
- `OPENCLAW_GATEWAY_URL` or `OPENCLAW_GATEWAY_URLS`

For OpenClaw gateway (if enabled):
- `SKYNET_AUTH_TOKEN` (optional but recommended)
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_ALLOWED_USER_ID` (if Telegram enabled)
- any model-provider keys required by OpenClaw runtime

## Verify

```bash
curl http://localhost:8000/v1/health
curl http://localhost:8000/v1/system-state
```

## Notes

- SKYNET does not execute workloads.
- OpenClaw runtime handles execution/tool/model/memory/session internals.
- Contract: `docs/SKYNET_OPENCLAW_CONTRACT.md`.
