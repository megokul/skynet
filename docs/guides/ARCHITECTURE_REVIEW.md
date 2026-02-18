# SKYNET/CHATHAN — Architecture Review

**Date**: 2026-02-15
**Status**: Pre-Implementation Review
**Purpose**: Identify risks, conflicts, and architectural decisions before building

---

## 🚨 Critical Architectural Issues

### **Issue #1: System Duplication & Overlap** — 🔴 BLOCKER

**Problem**: You have TWO systems with overlapping responsibilities:

#### **System A: openclaw-gateway/**
```
openclaw-gateway/
  ├── telegram_bot.py          ← Telegram interface
  ├── gateway.py                ← WebSocket gateway
  ├── chathan/                  ← CHATHAN protocol (duplicate?)
  │   ├── protocol/
  │   │   ├── plan_spec.py
  │   │   ├── execution_spec.py
  │   │   └── validation.py
  │   ├── execution/engine.py
  │   └── providers/
  ├── orchestrator/
  │   ├── scheduler.py
  │   ├── worker.py
  │   └── project_manager.py   ← High-level orchestration?
  ├── agents/                   ← Agent system
  ├── skills/                   ← Skill registry
  ├── db/                       ← Database
  └── sentinel/                 ← Monitoring
```

#### **System B: skynet/**
```
skynet/
  ├── gateway/
  │   └── telegram_bot.py       ← DUPLICATE Telegram interface
  ├── core/                     ← MISSING (needs to be built)
  │   ├── orchestrator.py       ← Would duplicate project_manager?
  │   ├── planner.py
  │   └── dispatcher.py
  ├── chathan/                  ← DUPLICATE CHATHAN protocol
  │   ├── protocol/
  │   ├── execution/engine.py
  │   └── providers/
  ├── ledger/                   ← Database (vs openclaw-gateway/db/)
  ├── queue/                    ← Celery queue
  ├── policy/                   ← Policy engine
  ├── sentinel/                 ← DUPLICATE monitoring
  └── archive/                  ← Log/artifact storage
```

**Questions**:
1. **Are these meant to be ONE system or TWO?**
2. **Should skynet/ replace openclaw-gateway/?**
3. **Or should they work together?**

---

### **DECISION REQUIRED: Choose ONE Architecture**

## **Option A: Merge Everything into SKYNET** (Recommended)

**Approach**: `skynet/` becomes the single authoritative system. Retire `openclaw-gateway/`.

```
┌─────────────────────────────────────────────────────────────┐
│                         SKYNET                              │
│                    (Single System)                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Telegram Bot ──→ Core Orchestrator ──→ CHATHAN Engine    │
│                         ↓                       ↓           │
│                    Policy Engine          Providers        │
│                         ↓                       ↓           │
│                   Ledger + Queue         OpenClaw Provider │
│                                                 ↓           │
│                                          OpenClaw Worker   │
│                                          (laptop agent)    │
└─────────────────────────────────────────────────────────────┘
```

**Migration Path**:
1. **Keep**: `openclaw-agent/` (worker) — no changes needed
2. **Consolidate into skynet/**:
   - Move `openclaw-gateway/ai/` → `skynet/ai/`
   - Move `openclaw-gateway/skills/` → `skynet/skills/`
   - Move `openclaw-gateway/agents/` → `skynet/agents/`
   - Move useful code from `openclaw-gateway/orchestrator/` → `skynet/core/`
3. **Delete**: `openclaw-gateway/` (after migration)
4. **Build**: Missing `skynet/core/` components
5. **Result**: One clean system

**Pros**:
✅ No duplication
✅ Single source of truth
✅ Cleaner architecture
✅ Matches the spec document

**Cons**:
❌ Requires migration effort
❌ Need to rewrite/consolidate existing code
❌ Risk of losing working features

---

## **Option B: Keep Separate — SKYNET as Orchestrator, OpenClaw as Gateway** (Clean Separation)

**Approach**: Clear separation of concerns with well-defined boundaries.

```
┌──────────────────────────────────────────────────────────────┐
│                      SKYNET (EC2)                            │
│                  Orchestration Layer                         │
├──────────────────────────────────────────────────────────────┤
│  Telegram Bot                                                │
│       ↓                                                      │
│  Orchestrator (plans, governs, tracks)                      │
│       ↓                                                      │
│  CHATHAN Protocol (ExecutionSpec generation)                │
│       ↓                                                      │
│  Policy Engine (safety checks)                              │
│       ↓                                                      │
│  Queue + Ledger (job management)                            │
│       ↓                                                      │
│  [HTTP POST to OpenClaw Gateway]                            │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        │ HTTP: POST /execute
                        ↓
┌──────────────────────────────────────────────────────────────┐
│                 OpenClaw Gateway (EC2)                       │
│                  Execution Gateway                           │
├──────────────────────────────────────────────────────────────┤
│  Accept ExecutionSpec                                        │
│       ↓                                                      │
│  Route to connected worker                                   │
│       ↓                                                      │
│  [WebSocket to Worker]                                       │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        │ WebSocket
                        ↓
┌──────────────────────────────────────────────────────────────┐
│              OpenClaw Worker (Laptop)                        │
│                  Execution Worker                            │
├──────────────────────────────────────────────────────────────┤
│  Execute shell commands                                      │
│  Execute Python code                                         │
│  File operations                                             │
│  Git operations                                              │
│  Return results                                              │
└──────────────────────────────────────────────────────────────┘
```

**System Boundaries**:

| System | Responsibility | Tech Stack |
|--------|---------------|------------|
| **SKYNET** | Orchestration, planning, policy, job management | Python, Celery, Redis, SQLite |
| **OpenClaw Gateway** | Worker connection management, WebSocket routing | Python, WebSocket, FastAPI |
| **OpenClaw Worker** | Command execution, file ops, git ops | Python, local execution |

**Communication**:
- SKYNET → OpenClaw Gateway: **HTTP REST API**
- OpenClaw Gateway → Worker: **WebSocket**
- Worker → Gateway → SKYNET: **Status/logs streaming**

**Pros**:
✅ Clean separation of concerns
✅ Can keep existing openclaw-gateway code
✅ SKYNET focuses on orchestration
✅ OpenClaw Gateway focuses on worker management
✅ Easier to scale independently

**Cons**:
❌ Two systems to maintain
❌ Network hop adds latency
❌ Duplication of some concepts (CHATHAN protocol in both?)
❌ More complex deployment

**Decision Needed**:
- Should CHATHAN protocol live in both?
- Or should OpenClaw Gateway be "dumb" and just forward specs?

---

## **Option C: Hybrid — Skynet as Library, OpenClaw Gateway as Runtime** (Minimal Change)

**Approach**: Refactor `skynet/` into a library that `openclaw-gateway/` imports.

```
skynet/                       ← Pure library (no main.py)
  ├── core/                   ← Orchestration logic
  ├── chathan/                ← Protocol definitions
  ├── policy/                 ← Policy engine
  ├── ledger/                 ← Data models
  └── queue/                  ← Queue integration

openclaw-gateway/             ← Runtime application
  ├── main.py                 ← Imports skynet.core
  ├── telegram_bot.py         ← Uses skynet.Orchestrator
  ├── gateway.py              ← WebSocket server
  └── ...
```

**Code**:
```python
# openclaw-gateway/main.py
from skynet.core import Orchestrator, Planner, Dispatcher
from skynet.policy import PolicyEngine
from skynet.ledger import Ledger

# Initialize SKYNET components
orchestrator = Orchestrator(...)

# Use in Telegram bot
telegram_bot.set_orchestrator(orchestrator)
```

**Pros**:
✅ Minimal restructuring
✅ Keep working openclaw-gateway
✅ Add SKYNET capabilities incrementally
✅ Shared code via library

**Cons**:
❌ Doesn't match spec (spec wants SKYNET as primary name)
❌ Unclear system identity
❌ Still has duplication

---

## **RECOMMENDATION: Option B (Clean Separation)**

### **Why?**

1. **Matches your spec** — Spec clearly defines SKYNET (orchestrator) and OpenClaw (execution provider)
2. **Scalability** — Can scale orchestration and execution independently
3. **Security** — SKYNET (EC2) never executes code directly; worker is isolated
4. **Flexibility** — Easy to add new providers (Docker, SSH, etc.) without touching SKYNET
5. **Clarity** — Each system has ONE job

### **What This Means**:

**SKYNET becomes**:
- ✅ Command intake (Telegram)
- ✅ Task planning (AI-powered)
- ✅ Safety & policy enforcement
- ✅ Job queue management
- ✅ State tracking & persistence
- ✅ Monitoring & alerts
- ❌ NO direct command execution
- ❌ NO WebSocket management

**OpenClaw Gateway becomes**:
- ✅ Worker connection management (WebSocket)
- ✅ ExecutionSpec routing to workers
- ✅ Live log streaming
- ✅ Worker health tracking
- ❌ NO planning or AI
- ❌ NO policy decisions
- ❌ NO Telegram interface

**OpenClaw Worker stays**:
- ✅ Execute commands on laptop
- ✅ Report back to gateway
- ✅ No changes needed

---

## 🔍 Detailed Architectural Concerns

### **Concern #2: AI Provider for Planning**

**Question**: Where does the AI live that generates PlanSpec?

**Options**:

**A) SKYNET calls Claude API directly**
```python
# skynet/core/planner.py
import anthropic

client = anthropic.Anthropic(api_key=...)
response = client.messages.create(
    model="claude-sonnet-4",
    messages=[{"role": "user", "content": planning_prompt}]
)
```

**Pros**: Simple, direct
**Cons**: SKYNET needs API key, costs on SKYNET infrastructure

**B) SKYNET uses openclaw-gateway's AI router**
```python
# skynet/core/planner.py
async def generate_plan(...):
    # Call openclaw-gateway's AI provider router
    response = await http_client.post(
        "http://openclaw-gateway:8766/ai/generate",
        json={"prompt": planning_prompt}
    )
```

**Pros**: Reuse existing AI infrastructure, quota management
**Cons**: Dependency on openclaw-gateway (conflicts with Option A/B separation)

**C) Both have their own AI clients**

**Pros**: Complete independence
**Cons**: Duplicate quota management, cost tracking

**Recommendation**: **Option A** — SKYNET has its own AI client for independence

---

### **Concern #3: Database Architecture**

**Current State**:
- `skynet/ledger/` — SQLite/Postgres for jobs, workers, locks
- `openclaw-gateway/db/` — Separate database for projects, agents

**Questions**:
1. Should these be **one database** or **two**?
2. If two, how do they stay in sync?

**Option A: Single Shared Database**
```
PostgreSQL
  ├── skynet_jobs
  ├── skynet_workers
  ├── skynet_locks
  ├── gateway_projects (if keeping openclaw-gateway)
  └── gateway_agents
```

**Pros**: Single source of truth, easy joins
**Cons**: Tight coupling

**Option B: Separate Databases**
```
SKYNET DB (PostgreSQL)          OpenClaw Gateway DB (SQLite)
  ├── jobs                        ├── worker_connections
  ├── workers                     ├── websocket_sessions
  └── locks                       └── execution_logs
```

**Pros**: Loose coupling, independent scaling
**Cons**: No joins, eventual consistency challenges

**Recommendation**:
- If **Option A (merge)**: Single database
- If **Option B (separation)**: Separate databases with API sync

---

### **Concern #4: Log/Artifact Streaming**

**Challenge**: Real-time log streaming from Worker → SKYNET → Telegram

**Current Flow**:
```
Worker → WebSocket → Gateway → ??? → SKYNET → Telegram
```

**Options**:

**A) Gateway buffers logs, SKYNET polls**
```python
# SKYNET periodically polls OpenClaw Gateway
logs = await gateway_client.get("/jobs/{job_id}/logs")
```

**Pros**: Simple
**Cons**: Latency, not real-time

**B) Gateway pushes logs to SKYNET via webhook**
```python
# Gateway sends webhook when logs arrive
await http_client.post(
    "http://skynet:8000/jobs/{job_id}/logs",
    json={"log_line": "..."}
)
```

**Pros**: Real-time
**Cons**: SKYNET needs HTTP server, more complex

**C) Shared message bus (Redis Pub/Sub)**
```python
# Worker publishes logs to Redis channel
redis.publish(f"job:{job_id}:logs", log_line)

# SKYNET subscribes to channel
async for message in redis.subscribe(f"job:{job_id}:logs"):
    await telegram.send_message(message)
```

**Pros**: Real-time, decoupled, scalable
**Cons**: Requires Redis (but you already have it for Celery)

**Recommendation**: **Option C (Redis Pub/Sub)** — Best for real-time streaming

---

### **Concern #5: Job Cancellation Flow**

**Challenge**: How does cancellation propagate?

**Flow**:
```
Telegram: /cancel job_123
    ↓
SKYNET: Update ledger (CANCELLED)
    ↓
SKYNET: Send cancel to provider
    ↓
OpenClaw Provider: POST /cancel/job_123
    ↓
OpenClaw Gateway: WebSocket to worker
    ↓
Worker: Kill process
    ↓
Worker: Ack cancel
    ↓
Gateway → SKYNET → Telegram: "Cancelled"
```

**Questions**:
1. What if worker is unreachable?
2. What if process can't be killed?
3. Timeout for cancel operation?

**Recommendation**:
- **Best-effort cancellation** — Set status to CANCELLED immediately
- Send cancel signal to provider (5s timeout)
- If worker doesn't ack, mark as "force cancelled"
- Show user "Cancel requested" vs "Cancel confirmed"

---

### **Concern #6: Worker Registry — Where?**

**Question**: Should worker registry live in SKYNET or OpenClaw Gateway?

**Option A: In SKYNET**
```python
# skynet/ledger/worker_registry.py
# Tracks all workers across all providers
```

**Pros**: Central view of all workers
**Cons**: Gateway must report heartbeats to SKYNET

**Option B: In OpenClaw Gateway**
```python
# openclaw-gateway/worker_registry.py
# Gateway tracks its own connected workers
```

**Pros**: Gateway owns worker connections
**Cons**: SKYNET can't see worker health

**Option C: Both (synced)**
- Gateway tracks live WebSocket connections
- SKYNET tracks worker registration via heartbeats from Gateway

**Recommendation**: **Option C** — Gateway manages connections, SKYNET tracks global state

---

### **Concern #7: ExecutionSpec Validation — Where?**

**Current plan**: CHATHAN validation in both skynet/ and openclaw-gateway/

**Question**: Who validates ExecutionSpec?

**Option A: SKYNET validates, Gateway trusts**
```python
# SKYNET
spec = dispatcher.create_execution_spec(...)
policy_engine.validate_execution(spec)  # ← Validation here
queue.enqueue(spec)

# Gateway
# Just execute whatever SKYNET sends (trusted)
```

**Pros**: Single validation point, Gateway is simpler
**Cons**: Security risk if Gateway accepts external requests

**Option B: Both validate (defense in depth)**
```python
# SKYNET validates before sending
policy_engine.validate(spec)

# Gateway validates on receive (safety check)
if not validator.is_safe(spec):
    reject()
```

**Pros**: Defense in depth, catches bugs
**Cons**: Duplicate validation logic

**Recommendation**: **Option B** — Validate in both (security critical)

---

### **Concern #8: Provider Interface Mismatch**

**Spec says**: BaseExecutionProvider with `execute()`, `health_check()`, `cancel()`

**Current implementation**: Provider is **async HTTP client** to OpenClaw Gateway

**Question**: Is OpenClaw Gateway a "provider" or is the laptop Worker a "provider"?

**Clarification Needed**:

```python
# Option A: Gateway is the provider
class OpenClawProvider(BaseExecutionProvider):
    """Calls OpenClaw Gateway HTTP API"""
    async def execute(self, spec):
        await http_client.post("http://gateway/execute", json=spec)

# Option B: Worker is the provider (Gateway is transparent)
class OpenClawWorkerProvider(BaseExecutionProvider):
    """Directly talks to worker via WebSocket (Gateway is proxy)"""
    async def execute(self, spec):
        # How? Gateway manages WebSocket, not SKYNET
```

**Recommendation**: **Option A** — OpenClaw Provider = HTTP client to Gateway

This means:
- "Provider" is the **interface** SKYNET talks to
- Gateway is **OpenClaw Provider's implementation**
- Worker is **execution backend** (invisible to SKYNET)

---

### **Concern #9: Queue Redundancy**

**Current plan**: Celery queue in SKYNET

**Question**: Do you need Celery if you have WebSocket workers?

**Celery Use Cases**:
- Distributed task queue
- Job persistence
- Retry logic
- Scheduled jobs
- Worker pooling

**Alternative**: Redis queue without Celery

```python
# Lightweight queue
import aioredis

# Enqueue
await redis.rpush("skynet:jobs:queue", job_id)

# Dequeue (in worker loop)
job_id = await redis.blpop("skynet:jobs:queue", timeout=5)
```

**Celery Pros**:
✅ Mature, battle-tested
✅ Built-in retry, scheduling, monitoring
✅ Worker pooling

**Celery Cons**:
❌ Heavyweight for simple use case
❌ Extra complexity
❌ You already have worker management (OpenClaw Gateway)

**Recommendation**:
- **Keep Celery** if you need:
  - Scheduled jobs (e.g., "run tests every hour")
  - Complex retry logic
  - Job priority queues
- **Use simple Redis queue** if you just need FIFO job dispatch

---

### **Concern #10: Failure Modes**

**What happens if...**

| Failure | Current Plan | Better Approach |
|---------|-------------|-----------------|
| SKYNET crashes | Jobs in queue lost | ✅ Celery persists jobs in Redis |
| Gateway crashes | Workers disconnected, jobs lost | ⚠️ Need job recovery on Gateway restart |
| Worker crashes | Job stuck in RUNNING | ✅ Sentinel detects via lock timeout |
| Redis crashes | Queue lost, locks lost | ⚠️ Need Redis persistence (AOF/RDB) |
| Database crashes | All state lost | ⚠️ Need DB backups |
| Network partition | SKYNET can't reach Gateway | ⚠️ Need timeout + retry logic |

**Missing**:
- ❌ Job recovery on Gateway crash
- ❌ Worker reconnection logic
- ❌ Graceful degradation (e.g., queue jobs if Gateway down)

**Recommendations**:
1. **Redis persistence**: Enable AOF for durability
2. **Gateway job recovery**: On startup, resume in-flight jobs
3. **Worker heartbeat**: 60s timeout, auto-reconnect
4. **SKYNET retry**: If Gateway unreachable, retry 3x before FAILED

---

## 🎯 Key Architectural Decisions Needed

### **Decision Matrix**

| # | Decision | Options | Recommendation | Priority |
|---|----------|---------|---------------|----------|
| 1 | System structure | Merge / Separate / Hybrid | **Separate (Option B)** | 🔴 CRITICAL |
| 2 | AI provider | SKYNET direct / Use Gateway / Both | **SKYNET direct** | 🔴 CRITICAL |
| 3 | Database | Shared / Separate | **Separate** | 🟡 HIGH |
| 4 | Log streaming | Poll / Webhook / Pub/Sub | **Redis Pub/Sub** | 🟡 HIGH |
| 5 | Worker registry | SKYNET / Gateway / Both | **Both (synced)** | 🟡 HIGH |
| 6 | Validation | SKYNET only / Both | **Both** | 🟡 HIGH |
| 7 | Provider definition | Gateway=provider / Worker=provider | **Gateway=provider** | 🟢 MEDIUM |
| 8 | Queue | Celery / Redis | **Celery (if complex) / Redis (if simple)** | 🟢 MEDIUM |
| 9 | Cancellation | Best-effort / Guaranteed | **Best-effort** | 🟢 MEDIUM |

---

## 📐 Recommended Final Architecture

Based on analysis, here's the **recommended production architecture**:

```
┌─────────────────────────────────────────────────────────────────┐
│                        SKYNET (EC2)                             │
│                     Control Plane                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────┐      ┌──────────────┐                       │
│  │ Telegram Bot  │─────→│ Orchestrator │                       │
│  └───────────────┘      └──────┬───────┘                       │
│                                 │                               │
│                    ┌────────────┼────────────┐                 │
│                    ↓            ↓            ↓                  │
│              ┌─────────┐  ┌──────────┐  ┌─────────┐            │
│              │ Planner │  │Dispatcher│  │ Policy  │            │
│              │  (AI)   │  │          │  │ Engine  │            │
│              └─────────┘  └──────────┘  └─────────┘            │
│                                 │                               │
│                    ┌────────────┼────────────┐                 │
│                    ↓            ↓            ↓                  │
│              ┌─────────┐  ┌──────────┐  ┌─────────┐            │
│              │ Ledger  │  │  Queue   │  │ Archive │            │
│              │  (DB)   │  │ (Celery) │  │ (Logs)  │            │
│              └─────────┘  └──────────┘  └─────────┘            │
│                                 │                               │
│                                 ↓                               │
│                    ┌────────────────────────┐                  │
│                    │ CHATHAN Engine         │                  │
│                    │  (Provider Router)     │                  │
│                    └───────────┬────────────┘                  │
│                                │                                │
└────────────────────────────────┼────────────────────────────────┘
                                 │
                                 │ HTTP: POST /execute
                                 ↓
┌─────────────────────────────────────────────────────────────────┐
│                   OpenClaw Gateway (EC2)                        │
│                      Data Plane                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────┐        ┌─────────────────┐               │
│  │  HTTP API        │───────→│ Worker Manager  │               │
│  │  /execute        │        │  (WebSocket)    │               │
│  │  /status         │        └────────┬────────┘               │
│  │  /cancel         │                 │                        │
│  └──────────────────┘                 │                        │
│                                       │                         │
│                          ┌────────────┼─────────────┐           │
│                          ↓                          ↓           │
│                  ┌──────────────┐          ┌──────────────┐    │
│                  │ Worker #1    │          │ Worker #2    │    │
│                  │ (WebSocket)  │          │ (WebSocket)  │    │
│                  └──────┬───────┘          └──────┬───────┘    │
│                         │                         │            │
└─────────────────────────┼─────────────────────────┼────────────┘
                          │                         │
                          │ WebSocket               │ WebSocket
                          ↓                         ↓
              ┌──────────────────┐      ┌──────────────────┐
              │ OpenClaw Worker  │      │ OpenClaw Worker  │
              │   (Laptop #1)    │      │   (Laptop #2)    │
              └──────────────────┘      └──────────────────┘
```

### **System Responsibilities (Final)**

| Component | Owns | Does NOT Own |
|-----------|------|--------------|
| **SKYNET** | Planning, policy, job state, queue, approvals, Telegram | WebSocket, worker connections, direct execution |
| **OpenClaw Gateway** | WebSocket management, worker routing, live logs | Planning, policy, job persistence |
| **OpenClaw Worker** | Command execution, file ops | Planning, approvals, state management |

### **Communication Protocols**

| From → To | Protocol | Purpose |
|-----------|----------|---------|
| Telegram → SKYNET | Telegram Bot API | Commands |
| SKYNET → Gateway | HTTP REST | Job dispatch |
| Gateway → Worker | WebSocket | Command execution |
| Worker → Gateway | WebSocket | Results, logs |
| Gateway → SKYNET | Redis Pub/Sub | Live log streaming |
| SKYNET → Telegram | Telegram Bot API | Updates |

---

## ✅ Action Items Before Implementation

### **Must Decide**:
1. ✅ **System structure**: Use Option B (Clean Separation)
2. ✅ **AI provider**: SKYNET has its own Claude client
3. ✅ **Database**: Separate databases (SKYNET DB, Gateway DB)
4. ✅ **Log streaming**: Redis Pub/Sub for real-time logs
5. ✅ **Worker registry**: Both systems maintain their view (synced via heartbeats)

### **Must Clarify**:
1. ❓ **Do you need project management?** (openclaw-gateway has project_manager)
   - If yes: Keep in SKYNET or separate?
   - If no: Remove from architecture

2. ❓ **Do you need multi-agent orchestration?** (openclaw-gateway has agents/)
   - If yes: How does this relate to SKYNET Core?
   - If no: Remove from architecture

3. ❓ **Skills vs Actions**: Clarify difference
   - Skills = high-level capabilities (git_commit, run_tests)
   - Actions = low-level ExecutionSpec steps (shell, python, file_write)

### **Must Build (in order)**:
1. 🔨 OpenClaw Gateway API additions (`/execute`, `/status/{job_id}`, `/cancel/{job_id}`)
2. 🔨 SKYNET Core (planner, dispatcher, orchestrator)
3. 🔨 OpenClaw Provider (HTTP client to Gateway)
4. 🔨 Redis Pub/Sub log streaming
5. 🔨 End-to-end integration

---

## 🎯 Next Steps

### **Option 1: Proceed with Recommended Architecture** (Option B - Clean Separation)
- I'll update the implementation plan to reflect this architecture
- Build SKYNET Core independently
- Update OpenClaw Gateway with required endpoints
- Keep systems decoupled

### **Option 2: Choose Different Architecture** (Option A or C)
- We'll revise the plan accordingly
- Discuss migration strategy if merging

### **Option 3: Answer Clarification Questions First**
- Decide on project management scope
- Decide on multi-agent scope
- Clarify skills vs actions

**Which option do you prefer?**
