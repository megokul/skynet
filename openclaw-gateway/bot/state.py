"""
SKYNET Bot — Global State Container

Holds references to shared resources (db, router) that are injected into
bot_data at startup and accessed by all handlers via context.bot_data.

Usage in handlers:
    db     = context.bot_data["db"]
    router = context.bot_data["router"]   # available for Chat feature
"""
from __future__ import annotations

# Keys used in context.bot_data — import these to avoid magic strings.
KEY_DB     = "db"
KEY_ROUTER = "router"
