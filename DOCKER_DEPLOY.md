# SKYNET - Docker Deployment Guide

Complete guide for deploying SKYNET Control Plane API using Docker.

---

## 🚀 Quick Start

### Prerequisites

- Docker 20.10+
- Docker Compose 2.0+
- Google Gemini API key ([Get one here](https://aistudio.google.com))

### 1. Clone and Setup

```bash
git clone <repository-url>
cd skynet

# Copy environment template
cp .env.example .env

# Edit .env and add your API keys
nano .env  # or use your favorite editor
```

### 2. Configure Environment

Edit `.env` file and set:

```bash
# Required
GOOGLE_AI_API_KEY=your_actual_gemini_key_here

# Optional (for OpenClaw integration)
SKYNET_AUTH_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
```

### 3. Build and Run

```bash
# Build images
docker-compose build

# Start services
docker-compose up -d

# Check logs
docker-compose logs -f skynet-api
```

### 4. Verify Deployment

```bash
# Health check
curl http://localhost:8000/v1/health

# Expected response:
# {
#   "status": "ok",
#   "version": "1.0.0",
#   "components": {
#     "planner": "ok",
#     "policy_engine": "ok"
#   }
# }
```

---

## 📋 Available Services

### SKYNET API (Port 8000)

**Endpoints:**
- `GET /` - Service information
- `GET /v1/health` - Health check
- `POST /v1/plan` - Generate execution plan
- `POST /v1/report` - Receive progress report
- `POST /v1/policy/check` - Validate action against policy
- `GET /docs` - Interactive API documentation (Swagger UI)

**Access:**
- Local: http://localhost:8000
- API Docs: http://localhost:8000/docs

### OpenClaw Gateway (Optional, Ports 8765/8766)

Uncomment the `openclaw-gateway` service in `docker-compose.yml` to enable.

---

## 🛠️ Docker Commands

### Start Services

```bash
# Start in background
docker-compose up -d

# Start with logs
docker-compose up

# Start specific service only
docker-compose up -d skynet-api
```

### Stop Services

```bash
# Stop all services
docker-compose down

# Stop and remove volumes (⚠️  deletes data!)
docker-compose down -v
```

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f skynet-api

# Last 100 lines
docker-compose logs --tail=100 skynet-api
```

### Restart Services

```bash
# Restart all
docker-compose restart

# Restart specific service
docker-compose restart skynet-api
```

### Rebuild After Code Changes

```bash
# Rebuild images
docker-compose build

# Rebuild without cache
docker-compose build --no-cache

# Rebuild and restart
docker-compose up -d --build
```

---

## 🔧 Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GOOGLE_AI_API_KEY` | Yes | - | Gemini API key for AI planning |
| `CORS_ORIGINS` | No | `*` | Allowed CORS origins (comma-separated) |
| `SKYNET_DB_PATH` | No | `/app/data/skynet.db` | Database file path |
| `SKYNET_AUTH_TOKEN` | No | - | Auth token for OpenClaw integration |

### Volumes

- `skynet-data` - Persistent data (database, logs)
  - Location: `/app/data` inside container
  - Persists across container restarts

### Ports

- `8000` - SKYNET API (HTTP)
- `8765` - OpenClaw Gateway WebSocket (optional)
- `8766` - OpenClaw Gateway HTTP API (optional)

---

## 📊 Monitoring

### Health Checks

Docker Compose includes automatic health checks:

```bash
# Check service health
docker-compose ps

# Healthy output shows:
# NAME         STATUS
# skynet-api   Up (healthy)
```

### Manual Health Check

```bash
# From host
curl http://localhost:8000/v1/health

# From inside container
docker-compose exec skynet-api curl http://localhost:8000/v1/health
```

---

## 🐛 Troubleshooting

### Service Won't Start

```bash
# Check logs
docker-compose logs skynet-api

# Common issues:
# 1. Missing GOOGLE_AI_API_KEY
# 2. Port 8000 already in use
# 3. Invalid .env file format
```

### Port Already in Use

```bash
# Find process using port 8000
netstat -ano | findstr :8000  # Windows
lsof -i :8000  # Linux/Mac

# Change port in docker-compose.yml:
ports:
  - "8001:8000"  # Use 8001 instead
```

### Container Crashes Immediately

```bash
# View startup logs
docker-compose logs skynet-api

# Run container interactively for debugging
docker-compose run --rm skynet-api /bin/bash
```

### API Key Not Working

```bash
# Verify .env file is loaded
docker-compose config

# Restart after changing .env
docker-compose down
docker-compose up -d
```

---

## 🚀 Production Deployment

### AWS EC2 Deployment

1. **Launch EC2 Instance**
   - Ubuntu 22.04 LTS
   - t3.small or larger
   - Open ports: 22 (SSH), 8000 (API)

2. **Install Docker**
   ```bash
   curl -fsSL https://get.docker.com -o get-docker.sh
   sudo sh get-docker.sh
   sudo usermod -aG docker ubuntu
   ```

3. **Clone and Deploy**
   ```bash
   git clone <repository-url>
   cd skynet
   cp .env.example .env
   # Edit .env with production values
   docker-compose up -d
   ```

4. **Set Up Reverse Proxy (Optional)**
   Use Nginx or Caddy for HTTPS:
   ```bash
   # Install Caddy
   sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
   sudo apt update
   sudo apt install caddy

   # Configure Caddy
   sudo nano /etc/caddy/Caddyfile
   ```

   ```caddy
   api.yourdomain.com {
       reverse_proxy localhost:8000
   }
   ```

### Environment Variables for Production

```bash
# Use specific CORS origins
CORS_ORIGINS=https://yourdomain.com,https://app.yourdomain.com

# Stronger auth token
SKYNET_AUTH_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
```

---

## 📦 Backup and Restore

### Backup Data

```bash
# Backup database
docker-compose exec skynet-api tar -czf /tmp/backup.tar.gz /app/data
docker cp skynet-api:/tmp/backup.tar.gz ./backup-$(date +%Y%m%d).tar.gz
```

### Restore Data

```bash
# Restore database
docker cp ./backup-20260218.tar.gz skynet-api:/tmp/backup.tar.gz
docker-compose exec skynet-api tar -xzf /tmp/backup.tar.gz -C /
docker-compose restart skynet-api
```

---

## 🔄 Updates

### Update to Latest Version

```bash
# Pull latest code
git pull

# Rebuild and restart
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# Verify
docker-compose logs -f skynet-api
```

---

## 📝 Notes

- Database is stored in Docker volume `skynet-data`
- Logs are written to container stdout (view with `docker-compose logs`)
- API documentation available at `/docs` endpoint
- Health checks run every 30 seconds

---

**Need help?** Check the main [README.md](README.md) or [CLAUDE.md](CLAUDE.md) for more information.
