# SKYNET — Telegram Bot Setup

**Status**: ✅ Implemented and tested (Phase 6.1)

This guide explains how to set up and use the SKYNET Telegram bot interface.

---

## 🤖 What is it?

The Telegram bot provides a chat interface for SKYNET, allowing you to:
- Create tasks with natural language
- Review AI-generated plans
- Approve/deny tasks before execution
- Monitor job status
- Manage your task queue

---

## 📋 Prerequisites

1. **Telegram Account**: You need a Telegram account
2. **Python Packages**: Already installed if you ran `pip install python-telegram-bot`
3. **SKYNET Core**: Phase 1 must be complete (Planner, Dispatcher, Orchestrator, Main)

---

## 🚀 Setup Instructions

### Step 1: Create a Telegram Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` command
3. Follow the instructions:
   - Choose a name for your bot (e.g., "My SKYNET Bot")
   - Choose a username (must end in 'bot', e.g., "my_skynet_bot")
4. BotFather will give you a **bot token** - save this!

Example token: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`

### Step 2: Get Your Telegram User ID

1. Search for `@userinfobot` on Telegram
2. Send `/start`
3. It will reply with your user ID (a number like `12345678`)
4. Save this number

### Step 3: Configure Environment

Edit your `.env` file and add:

```bash
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN_HERE
TELEGRAM_ALLOWED_USER_ID=YOUR_USER_ID_HERE
```

**Example:**
```bash
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_ALLOWED_USER_ID=12345678
```

---

## ▶️ Running the Bot

```bash
python run_telegram.py
```

You should see:
```
======================================================================
SKYNET - Ready!
======================================================================

Open Telegram and send /start to your bot
Press Ctrl+C to stop
======================================================================
```

---

## 💬 Using the Bot

### Available Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/start` | Welcome message and help | `/start` |
| `/help` | Show help message | `/help` |
| `/task <description>` | Create a new task | `/task Check git status` |
| `/status <job_id>` | Check job status | `/status job_abc123` |
| `/list` | List recent jobs | `/list` |
| `/cancel <job_id>` | Cancel a job | `/cancel job_abc123` |

### Example Workflow

1. **Create a task:**
   ```
   /task Check git status and list all modified files
   ```

2. **Bot generates a plan and shows it to you:**
   ```
   📋 Plan Generated

   Intent: Check git status and list all modified files

   Summary: Navigate to project directory and execute git status...

   Risk Level: READ_ONLY
   Approval Required: No

   Steps (2):
   1. Navigate to Project Directory [READ_ONLY]
   2. Execute Git Status Command [READ_ONLY]

   ✅ Auto-approved (READ_ONLY)
   Job ID: job_abc123
   Status: QUEUED
   ```

3. **For WRITE/ADMIN tasks, you'll get approval buttons:**
   ```
   📋 Plan Generated

   Intent: Deploy bot to production

   Risk Level: ADMIN
   Approval Required: Yes

   Steps (5):
   1. Run tests [READ_ONLY]
   2. Build Docker image [WRITE]
   3. Push to registry [WRITE]
   4. Deploy to production [ADMIN]
   5. Verify deployment [READ_ONLY]

   [✅ Approve]  [❌ Deny]
   ```

4. **Check status:**
   ```
   /status job_abc123
   ```

5. **List all jobs:**
   ```
   /list
   ```

---

## 🔒 Security Features

- **Single User**: Only the configured `TELEGRAM_ALLOWED_USER_ID` can use the bot
- **Auto-Approval**: READ_ONLY tasks are auto-approved (safe operations)
- **Manual Approval**: WRITE/ADMIN tasks require explicit approval
- **Risk Classification**: All tasks are classified by risk level

---

## 🐛 Troubleshooting

### Bot doesn't respond

1. Check bot is running: `python run_telegram.py`
2. Verify token is correct in `.env`
3. Make sure your user ID matches `TELEGRAM_ALLOWED_USER_ID`

### "Unauthorized access" message

- Your Telegram user ID doesn't match `TELEGRAM_ALLOWED_USER_ID`
- Get your ID from `@userinfobot` and update `.env`

### Bot starts but commands don't work

- Make sure you're sending commands to the correct bot
- Try `/start` first to verify connection

---

## 🔮 Future Interfaces

Currently supported:
- ✅ Telegram (Primary)

Planned:
- ⏳ WhatsApp
- ⏳ Voice/Audio interface
- ⏳ Web UI
- ⏳ API endpoints

All interfaces will use the same SKYNET core (Planner, Dispatcher, Orchestrator).

---

## 📚 Technical Details

### Architecture

```
Telegram User
     ↓
SkynetTelegramBot
     ↓
SkynetApp (Main)
     ↓
Orchestrator → Planner + Dispatcher
     ↓
Job Queue (Mock for now, Celery later)
```

### Files

- `skynet/telegram/bot.py` - Telegram bot implementation
- `run_telegram.py` - Startup script
- `test_telegram.py` - Initialization tests

### Dependencies

- `python-telegram-bot` - Telegram Bot API wrapper

---

**Questions?** Check `AGENT_GUIDE.md` or `CLAUDE.md` for more details.
