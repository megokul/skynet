#!/usr/bin/env bash
# ---------------------------------------------------------------
# OpenClaw Gateway — EC2 Deployment Script (Docker Compose)
#
# Run this ON the EC2 instance after git pull.
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
#
# Prerequisites:
#   - Docker + Docker Compose installed on EC2
#   - SKYNET_AUTH_TOKEN set in environment or .env file
#   - TELEGRAM_BOT_TOKEN set in environment or .env file
# ---------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}"

echo "============================================"
echo "  OpenClaw Gateway — Deployment"
echo "============================================"
echo ""

# --- 1. Verify required secrets ---
echo "[1/4] Checking required environment variables…"
if [ -z "${SKYNET_AUTH_TOKEN:-}" ]; then
    TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
    echo ""
    echo "  ┌──────────────────────────────────────────────────┐"
    echo "  │  SKYNET_AUTH_TOKEN not set — generated one:      │"
    echo "  │                                                   │"
    echo "  │  ${TOKEN}"
    echo "  │                                                   │"
    echo "  │  Add to your .env or export it, then rerun.      │"
    echo "  │  Set this SAME token on your CLAW worker.        │"
    echo "  └──────────────────────────────────────────────────┘"
    echo ""
    exit 1
fi

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
    echo "  ERROR: TELEGRAM_BOT_TOKEN is not set."
    echo "  Get one from @BotFather on Telegram and export it."
    exit 1
fi

echo "  Auth token: set ✓"
echo "  Telegram token: set ✓"

# --- 2. TLS certificates ---
echo ""
echo "[2/4] TLS certificate check…"
CERT_DIR="${SCRIPT_DIR}/certs"
mkdir -p "${CERT_DIR}"
if [ ! -f "${CERT_DIR}/cert.pem" ] || [ ! -f "${CERT_DIR}/key.pem" ]; then
    echo "  Generating self-signed TLS certificate…"
    openssl req -x509 -newkey rsa:4096 -keyout "${CERT_DIR}/key.pem" \
        -out "${CERT_DIR}/cert.pem" -days 3650 -nodes \
        -subj "/CN=skynet-gateway"
    chmod 600 "${CERT_DIR}/key.pem"
    echo "  TLS cert generated: ${CERT_DIR}/cert.pem"
else
    echo "  TLS cert already exists ✓"
fi

# --- 3. Build and start ---
echo ""
echo "[3/4] Building Docker image…"
docker compose build

echo ""
echo "[4/4] Starting gateway…"
docker compose up -d

echo ""
echo "============================================"
echo "  Deployment complete!"
echo "============================================"
echo ""
echo "  Useful commands:"
echo "    docker compose logs -f"
echo "    docker compose ps"
echo "    docker compose restart gateway"
echo ""
echo "  Your CLAW worker should connect to:"
echo "    wss://<EC2_PUBLIC_IP>:8765"
echo ""
echo "  Don't forget to open port 8765 in your EC2 Security Group!"
echo ""
