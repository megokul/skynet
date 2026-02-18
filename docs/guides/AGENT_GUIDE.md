# SKYNET — AI Coding Agent Guide

**For**: Claude Code, Cursor, Copilot, or any AI coding assistant
**Purpose**: Pick up development seamlessly from where previous agent left off
**Last Updated**: 2026-02-16

> **🚨 READ THIS FIRST**: [POLICY.md](POLICY.md) - MANDATORY 5-file update rule
> After any significant change, you MUST update: CLAUDE.md, TODO.md, SESSION_NOTES.md, AGENT_GUIDE.md, DEVELOPMENT.md

---

## 🚀 Quick Start for New Agent Session

### **1. First, Read These Files (In Order)**

1. **[CLAUDE.md](CLAUDE.md)** - Current project state, what's built, what's not
2. **[TODO.md](TODO.md)** - Prioritized task list with clear next steps
3. **[DEVELOPMENT.md](DEVELOPMENT.md)** - Code patterns and conventions to follow
4. **This file** - How to work on this project

### **2. Check Current Environment**

```bash
# Verify you're in project root
pwd  # Should be: /e/MyProjects/skynet

# Check if venv is activated
which python  # Should point to ./venv/Scripts/python

# If not activated:
source venv/Scripts/activate  # Mac/Linux
# OR
venv\Scripts\activate  # Windows

# Verify dependencies
pip list | grep -E "google-genai|python-dotenv"
```

### **3. Understand What Works**

Run the test to verify the Planner is working:
```bash
python tests/test_planner_simple.py
python tests/test_dispatcher.py
```

**Expected**: Plan generated with 3 steps, risk level READ_ONLY

### **4. Check the Last Session**

Read the "Change Log" section at the bottom of [CLAUDE.md](CLAUDE.md) to see what was done most recently.

---

## 📋 Your Responsibilities

### **Before Starting Any Work**

- [ ] Read [TODO.md](TODO.md) to see current priorities
- [ ] Check [CLAUDE.md](CLAUDE.md) for project status
- [ ] Review [DEVELOPMENT.md](DEVELOPMENT.md) for coding patterns
- [ ] Verify tests pass: `python tests/test_planner_simple.py`, `python tests/test_dispatcher.py`, `python tests/test_orchestrator.py`

### **While Working**

- [ ] Follow code patterns from [DEVELOPMENT.md](DEVELOPMENT.md)
- [ ] Write tests for new components
- [ ] Update [TODO.md](TODO.md) as you complete tasks
- [ ] Keep [CLAUDE.md](CLAUDE.md) current with changes

### **After Completing Work**

- [ ] Run all tests to verify nothing broke
- [ ] Update [CLAUDE.md](CLAUDE.md):
  - Mark component as ✅ COMPLETED
  - Update "Last Updated" date
  - Add entry to "Change Log"
- [ ] Update [TODO.md](TODO.md):
  - Mark completed tasks as [x]
  - Add any new tasks discovered
- [ ] Update [SESSION_NOTES.md](SESSION_NOTES.md):
  - Document what was built
  - Note any decisions made
  - List any blockers or issues

---

## 🏗️ Current Build State

### **What's Working** ✅

| Component | Status | Location | Test File |
|-----------|--------|----------|-----------|
| Planner | ✅ Complete | `skynet/core/planner.py` | `tests/test_planner_simple.py` |
| Dispatcher | ✅ Complete | `skynet/core/dispatcher.py` | `tests/test_dispatcher.py` |
| Orchestrator | ✅ Complete | `skynet/core/orchestrator.py` | `tests/test_orchestrator.py` |
| Main Entry Point | ✅ Complete | `skynet/main.py` | `tests/test_main.py`, `scripts/run_demo.py` |
| Telegram Bot | ✅ Complete | `skynet/telegram/bot.py` | `tests/test_telegram.py`, see `TELEGRAM_SETUP.md` |
| Worker Steps Spec Compatibility | Complete | `skynet/queue/worker.py` | `tests/test_worker_steps_format.py` |
| Mock Provider | ✅ Complete | `skynet/chathan/providers/mock_provider.py` | (tested via worker) |
| Local Provider | ✅ Complete | `skynet/chathan/providers/local_provider.py` | `tests/test_local_provider.py` |
| Worker Registry | ✅ Complete | `skynet/ledger/worker_registry.py` | `tests/test_worker_registry.py` |
| Job Locking | ✅ Complete | `skynet/ledger/job_locking.py` | `tests/test_job_locking.py` |
| Gemini Client | ✅ Complete | `skynet/ai/gemini_client.py` | (tested via Planner) |
| Project Structure | ✅ Set up | Root directory | N/A |
| Documentation | ✅ Complete | `*.md` files | N/A |

### **What's Next** 🚧

🎉 **PHASE 7 SCENARIO COVERAGE COMPLETE!** End-to-end workflow cases are now validated.

See [TODO.md](TODO.md) for prioritized list. Next options:
1. **Provider routing hardening** (Phase 6.3) - validate provider failures and fallback behavior
2. **OpenClaw provider** (Phase 5) - add real gateway-backed execution provider
3. **More Providers** (Phase 5) - DockerProvider, SSHProvider

---

## 📚 Reference Documentation

### **Implementation Guides**
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) - Full 8-phase roadmap
- [LEARNING_IMPLEMENTATION_PLAN.md](LEARNING_IMPLEMENTATION_PLAN.md) - Detailed Phase 1 guide
- [QUICK_START.md](QUICK_START.md) - How we built the Planner

### **Architecture**
- [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) - Design decisions and alternatives

### **Development**
- [DEVELOPMENT.md](DEVELOPMENT.md) - Code patterns, conventions, best practices
- [TODO.md](TODO.md) - Task list with priorities

### **Session Tracking**
- [SESSION_NOTES.md](SESSION_NOTES.md) - History of all sessions
- [CLAUDE.md](CLAUDE.md) - Current project context

---

## 🎯 Common Tasks

### **Building a New Component**

1. **Check the plan first**
   ```bash
   # See IMPLEMENTATION_PLAN.md for spec
   # See LEARNING_IMPLEMENTATION_PLAN.md for details
   ```

2. **Create the file**
   ```bash
   # Follow existing structure
   touch skynet/core/dispatcher.py
   ```

3. **Follow patterns from existing code**
   - Look at `skynet/core/planner.py` as example
   - Match logging style: `logger = logging.getLogger("skynet.core.dispatcher")`
   - Use type hints: `def func(arg: str) -> dict[str, Any]:`
   - Add docstrings: Google style

4. **Write a test**
   ```bash
   # Create test in tests/
   touch tests/test_dispatcher.py

   # Follow pattern from tests/test_planner_simple.py
   # Keep it simple, no emojis (Windows encoding issues)
   ```

5. **Run the test**
   ```bash
   python tests/test_dispatcher.py
   ```

6. **Update documentation**
   - Mark component complete in [CLAUDE.md](CLAUDE.md)
   - Update [TODO.md](TODO.md)
   - Add to [SESSION_NOTES.md](SESSION_NOTES.md)

### **Testing Existing Components**

```bash
# Planner
python tests/test_planner_simple.py
python tests/test_dispatcher.py

# When Dispatcher is built:
python tests/test_dispatcher.py

# When Orchestrator is built:
python tests/test_orchestrator.py

# Full integration test (future):
python tests/test_integration.py
```

### **Checking API Quota**

```bash
# If you get 429 errors (rate limit):
# - Wait 40 seconds
# - Or try different model in planner.py
# - Check quota at: https://ai.dev/rate-limit
```

---

## 🔧 Troubleshooting

### **"ImportError: cannot import name 'genai'"**
```bash
# Install correct package
pip install google-genai
# NOT google-generativeai
```

### **"GOOGLE_AI_API_KEY not set"**
```bash
# Check .env file exists in project root
ls -la .env

# Verify key is set
grep GOOGLE_AI_API_KEY .env
```

### **"Virtual environment not found"**
```bash
# Create it
python -m venv venv

# Activate it
venv\Scripts\activate  # Windows

# Install dependencies
pip install google-genai python-dotenv
```

### **Tests failing with Unicode errors**
```bash
# Use the simple test file (no emojis)
python tests/test_planner_simple.py
python tests/test_dispatcher.py

# NOT tests/test_planner.py (has emoji encoding issues)
```

---

## 📐 Code Architecture Rules

### **Directory Structure Rules**

```
MUST follow this structure:

skynet/                    ← Python package
  core/                    ← Core components (planner, dispatcher, orchestrator)
  chathan/                 ← Execution protocol
  policy/                  ← Safety & risk
  ledger/                  ← State management
  queue/                   ← Job queue
  sentinel/                ← Monitoring
  archive/                 ← Logs & artifacts
  shared/                  ← Utilities

tests/test_*.py            ← Tests in tests/, not in package
*.md                       ← Documentation in ROOT
```

### **Import Rules**

```python
# Test files (in tests/)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.core.planner import Planner  # Now works

# Package files (in skynet/)
from skynet.shared.logging import setup_logger  # Absolute imports
```

### **Naming Rules**

- Files: `snake_case.py`
- Classes: `PascalCase`
- Functions: `snake_case()`
- Constants: `UPPER_CASE`
- Private: `_leading_underscore`

### **Documentation Rules**

Every significant change MUST update:
1. [CLAUDE.md](CLAUDE.md) - Project status
2. [TODO.md](TODO.md) - Task list
3. [SESSION_NOTES.md](SESSION_NOTES.md) - Session history

---

## 🎓 Learning from openclaw-gateway

The `openclaw-gateway/` directory contains working reference implementations:

### **When to Reference It**

✅ **Good uses**:
- See how ProjectManager works → Reference for Orchestrator
- Understand AI planning → Reference for Planner improvements
- Learn Gemini API → Reference for API patterns

❌ **Bad uses**:
- Don't copy code blindly → Understand then rewrite
- Don't use it directly → We're building fresh
- Don't match its structure → We have our own architecture

### **Key Files to Study**

| For Building | Study This | Location |
|--------------|------------|----------|
| Dispatcher | How plans convert to actions | `openclaw-gateway/orchestrator/project_manager.py` |
| Orchestrator | Project lifecycle management | `openclaw-gateway/orchestrator/scheduler.py` |
| AI Integration | Multi-provider routing | `openclaw-gateway/ai/provider_router.py` |
| Telegram Bot | Command handling | `openclaw-gateway/telegram_bot.py` |

---

## 🚨 Critical Rules

### **Never Do This**

❌ Change API key in .env without user permission
❌ Delete or move venv/ directory
❌ Skip updating documentation after changes
❌ Copy code from openclaw-gateway without understanding
❌ Use emojis in test output (Windows encoding issues)
❌ Commit .env file (it's in .gitignore)

### **Always Do This**

✅ Activate venv before running code
✅ Run tests after making changes
✅ Update CLAUDE.md when completing components
✅ Follow code patterns from DEVELOPMENT.md
✅ Ask user before major architectural changes
✅ Document decisions in SESSION_NOTES.md

---

## 🎯 Success Metrics

You're doing well if:

- ✅ Tests pass after your changes
- ✅ CLAUDE.md is up to date
- ✅ TODO.md reflects current state
- ✅ Code follows established patterns
- ✅ New components have tests
- ✅ Documentation is clear and accurate

---

## 💬 Communication

### **When to Ask User**

- Architecture decision with multiple valid options
- API key or credentials needed
- Major change to existing working code
- Uncertainty about requirements
- Encountering unexpected blockers

### **When to Proceed**

- Following established patterns
- Implementing from clear specs in IMPLEMENTATION_PLAN.md
- Writing tests for new code
- Updating documentation
- Bug fixes to failing tests

---

## 📝 Session Checklist

Use this checklist for every session:

### **Session Start**
- [ ] Read CLAUDE.md for current status
- [ ] Review TODO.md for priorities
- [ ] Check last session in SESSION_NOTES.md
- [ ] Verify tests pass: `python tests/test_planner_simple.py`
- [ ] Verify tests pass: `python tests/test_dispatcher.py`
- [ ] Activate venv

### **During Session**
- [ ] Follow patterns from DEVELOPMENT.md
- [ ] Write tests as you build
- [ ] Update TODO.md as tasks complete
- [ ] Document decisions

### **Session End**
- [ ] Run all tests
- [ ] Update CLAUDE.md (status, change log)
- [ ] Update TODO.md (completed tasks)
- [ ] Add session entry to SESSION_NOTES.md
- [ ] Commit changes (if using git)

---

## 🔗 Quick Links

- **Current State**: [CLAUDE.md](CLAUDE.md)
- **What to Build**: [TODO.md](TODO.md)
- **How to Build**: [DEVELOPMENT.md](DEVELOPMENT.md)
- **Full Roadmap**: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
- **Session History**: [SESSION_NOTES.md](SESSION_NOTES.md)

---

**Welcome to the SKYNET project! Follow this guide and you'll be productive immediately.** 🚀

If you're a future Claude Code session, start by reading CLAUDE.md, then TODO.md, then come back here for guidance.








