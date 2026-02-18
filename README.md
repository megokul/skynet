# SKYNET — Autonomous Task Orchestration System

**Status**: ✅ 100% Complete - All Phases Implemented
**Last Updated**: 2026-02-16

## 🎯 Overview

SKYNET is an autonomous task orchestration system with AI-powered planning, capable of decomposing user intent into executable tasks and routing them to appropriate execution providers.

### Key Features

- **AI-Powered Planning**: Uses Google Gemini to convert natural language into structured execution plans
- **Multiple Execution Providers**: Local shell, Docker containers, SSH remote, OpenClaw Gateway
- **Safety & Risk Management**: Automatic risk classification and approval workflows
- **Provider Monitoring**: Health tracking for all execution providers
- **Artifact & Log Storage**: Complete audit trail of executions
- **Telegram Interface**: Chat-based task management
- **Distributed Job Locking**: Prevents duplicate execution across workers

## Project Structure

```text
skynet/
|-- skynet/                   # Core package
|   |-- core/                # Planner, Dispatcher, Orchestrator
|   |-- chathan/             # Execution protocol and providers
|   |-- ledger/              # Job state and worker registry
|   |-- sentinel/            # Health monitoring and alerts
|   |-- archive/             # Artifact and log storage
|   |-- telegram/            # Telegram bot interface
|   |-- queue/               # Celery worker
|   `-- policy/              # Safety and risk rules
|
|-- tests/                   # Automated pytest suite
|-- scripts/                 # Utility scripts
|   |-- dev/
|   |   `-- run_api.py       # FastAPI dev startup
|   |-- manual/              # Manual integration checks
|   |   |-- check_api.py
|   |   |-- check_e2e_integration.py
|   |   `-- check_skynet_delegate.py
|   |-- run_telegram.py
|   |-- run_demo.py
|   `-- list_models.py
|
|-- docs/                    # Documentation
|-- openclaw-agent/          # Reference worker implementation
|-- openclaw-gateway/        # Reference gateway implementation
|-- README.md
|-- Makefile
|-- pytest.ini
`-- requirements.txt
```

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Google Gemini API key
- Redis (for Celery queue)
- Docker (optional, for DockerProvider)

### Installation

```bash
# Clone repository
cd e:\MyProjects\skynet

# Activate virtual environment
venv\Scripts\activate  # Windows
source venv/bin/activate  # Unix

# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env and add your GOOGLE_AI_API_KEY
```

### Running Tests

```bash
# Using Makefile (recommended)
make test              # Run core tests (fast)
make test-all          # Run all tests
make test-e2e          # Run end-to-end tests
make smoke             # Quick repo health checks

# Using pytest directly
python -m pytest tests/ -v

# Run specific test files
python tests/test_planner.py
python tests/test_worker.py
python tests/test_e2e.py

# Run with real providers (optional)
TEST_WITH_REAL_DOCKER=1 python tests/test_docker_provider.py
TEST_WITH_REAL_SSH=1 python tests/test_ssh_provider.py

# Without make (Windows-friendly)
python scripts/dev/smoke.py
```

### Running the System

```bash
# Using Makefile (recommended)
make run-api          # Start FastAPI service
make run-bot          # Start Telegram bot
make run-demo         # Run interactive demo
make run-worker       # Start Celery worker

# Or directly
python scripts/dev/run_api.py
python scripts/run_telegram.py
python scripts/run_demo.py
celery -A skynet.queue.worker worker --loglevel=info
```

### Manual Integration Checks

```bash
make manual-check-api
make manual-check-e2e
make manual-check-delegate
```

## 📚 Documentation

| Document | Purpose |
|----------|---------|
| [CLAUDE.md](CLAUDE.md) | Complete project context for AI agents |
| [QUICK_START.md](QUICK_START.md) | 30-minute tutorial |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Full 8-phase build plan |
| [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) | Architecture decisions |
| [TODO.md](TODO.md) | Task tracking (100% complete) |
| [SESSION_NOTES.md](SESSION_NOTES.md) | Development history (13 sessions) |
| [TELEGRAM_SETUP.md](TELEGRAM_SETUP.md) | Telegram bot setup guide |
| [AGENT_GUIDE.md](AGENT_GUIDE.md) | Guide for AI coding agents |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Code patterns & conventions |
| [POLICY.md](POLICY.md) | Documentation update policy |
| [LEARNING_IMPLEMENTATION_PLAN.md](LEARNING_IMPLEMENTATION_PLAN.md) | Learning-focused guide |

## 🏗️ Architecture

### Task Flow

```
User Intent (Natural Language)
         ↓
    Planner (AI-powered)
         ↓
    PlanSpec (Human-readable)
         ↓
    User Approval
         ↓
    Dispatcher
         ↓
    ExecutionSpec (Machine-executable)
         ↓
    Policy Validation
         ↓
    Celery Queue
         ↓
    Worker + Provider
         ↓
    Execution Results
         ↓
    Artifact & Log Storage
```

### Execution Providers

1. **MockProvider** - Testing without side effects
2. **LocalProvider** - Local shell command execution
3. **ChathanProvider** - Remote execution via OpenClaw Gateway
4. **DockerProvider** - Containerized isolated execution
5. **SSHProvider** - Remote execution via SSH

### Control Plane API (FastAPI)

- `POST /v1/plan` - Generate execution plan
- `POST /v1/report` - Report execution progress
- `POST /v1/policy/check` - Validate policy/risk
- `POST /v1/execute` - Direct synchronous execution (bypass queue)
- `POST /v1/scheduler/diagnose` - Provider scoring diagnostics
- `GET /v1/providers/health` - Provider health dashboard
- `POST /v1/memory/store` - Store memory record
- `POST /v1/memory/search` - Search memories
- `POST /v1/memory/similar` - Semantic similarity search
- `GET /v1/memory/stats` - Memory statistics
- `GET /v1/health` - Service health

#### Scheduler Diagnostics Example

Request:

```json
{
  "execution_spec": {
    "job_id": "job-123",
    "steps": [
      {"action": "run_tests", "params": {"working_dir": "."}}
    ]
  },
  "fallback": "local"
}
```

Response (abridged):

```json
{
  "selected_provider": "local",
  "fallback_used": false,
  "required_capabilities": ["run_tests"],
  "candidates": ["local", "mock"],
  "scores": [
    {
      "provider": "local",
      "total_score": 0.91,
      "health_score": 1.0,
      "load_score": 0.8,
      "capability_score": 1.0,
      "success_score": 0.7,
      "latency_score": 0.6
    }
  ]
}
```

#### Provider Health Dashboard Example

Request:

```bash
curl -X GET http://localhost:8000/v1/providers/health
```

Response (abridged):

```json
{
  "status": "degraded",
  "healthy_count": 1,
  "unhealthy_count": 1,
  "total_count": 2,
  "providers": {
    "local": {"status": "healthy", "message": "OK"},
    "docker": {"status": "unhealthy", "message": "daemon unavailable"}
  },
  "history": [
    {"timestamp": 1739890000.0}
  ]
}
```

## 🧪 Testing

- **Total Test Files**: 21
- **Total Test Scenarios**: 150+
- **Test Coverage**: 100% of implemented features
- **All Tests**: ✅ Passing

### Test Categories

- **Unit Tests**: Individual component testing
- **Integration Tests**: Cross-component workflows
- **E2E Tests**: Complete user journey testing
- **Provider Tests**: Each provider thoroughly tested

## 📊 Implementation Status

```
Phase 1: SKYNET Core      [████████████] 100% ✅
Phase 2: Ledger           [████████████] 100% ✅
Phase 3: Archive          [████████████] 100% ✅
Phase 4: Sentinel         [████████████] 100% ✅
Phase 5: Providers        [████████████] 100% ✅
Phase 6: Integration      [████████████] 100% ✅
Phase 7: Testing          [████████████] 100% ✅

Overall:                  [████████████] 100% 🎉
```

## 🔧 Configuration

### Environment Variables

```bash
# Core
GOOGLE_AI_API_KEY=your_gemini_api_key

# Worker
SKYNET_WORKER_ID=worker-hostname
SKYNET_DB_PATH=data/skynet.db
SKYNET_ALLOWED_PATHS=/path/to/allowed/dir

# Providers
SKYNET_DOCKER_IMAGE=ubuntu:22.04
SKYNET_SSH_HOST=localhost
SKYNET_SSH_PORT=22
SKYNET_SSH_USERNAME=ubuntu
SKYNET_SSH_KEY_PATH=/path/to/key

# OpenClaw Gateway
OPENCLAW_GATEWAY_URL=http://127.0.0.1:8766

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_USER_ID=your_user_id
```

## 🎓 Learning Journey

This project was built from scratch as a learning experience, following a systematic 8-phase plan:

1. **Phase 1**: Core orchestration (Planner, Dispatcher, Orchestrator)
2. **Phase 2**: Persistent ledger (Worker registry, Job locking)
3. **Phase 3**: Archive system (Artifacts, Logs)
4. **Phase 4**: Sentinel monitoring (Provider health, Alerts)
5. **Phase 5**: Execution providers (5 different providers)
6. **Phase 6**: Integration (Telegram bot, Worker wiring)
7. **Phase 7**: End-to-end testing

Total development time: 13 sessions over 2 days

## 🤝 Contributing

This is a learning project. See [DEVELOPMENT.md](DEVELOPMENT.md) for code patterns and conventions.

## 📝 License

Private project for learning purposes.

## 🙏 Acknowledgments

- Built with guidance from Claude Code (Anthropic)
- Reference implementations from openclaw-agent and openclaw-gateway
- AI planning powered by Google Gemini

---

**For detailed project context**: See [CLAUDE.md](CLAUDE.md)
**For development history**: See [SESSION_NOTES.md](SESSION_NOTES.md)
**To get started**: See [QUICK_START.md](QUICK_START.md)
