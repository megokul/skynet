"""
CHATHAN Worker — Resource Locks

Named asyncio locks that serialize heavy operations to prevent
collisions when multiple projects run in parallel. For example,
two concurrent ``npm install`` calls would corrupt node_modules.

Lock acquisition happens in the action router, wrapping execution.
Locks are in-memory and auto-release if the agent process crashes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("chathan.executor.locks")

# Named locks — each protects a shared resource.
_locks: dict[str, asyncio.Lock] = {
    "npm_install": asyncio.Lock(),
    "pip_install": asyncio.Lock(),
    "git": asyncio.Lock(),
    "build": asyncio.Lock(),
    "port": asyncio.Lock(),
    "ollama": asyncio.Lock(),  # GPU contention — serialize inference.
}


def _resolve_install_lock(params: dict[str, Any]) -> str:
    """Pick npm or pip lock based on the manager parameter."""
    manager = params.get("manager", "pip")
    return "npm_install" if manager == "npm" else "pip_install"


# Action → lock name resolver.
# Callables receive params and return the lock name.
# Strings are used directly.
ACTION_LOCK_MAP: dict[str, str | callable] = {
    "install_dependencies": _resolve_install_lock,
    "git_init": "git",
    "git_add_all": "git",
    "git_commit": "git",
    "git_push": "git",
    "gh_create_repo": "git",
    "build_project": "build",
    "docker_build": "build",
    "docker_compose_up": "build",
    "start_dev_server": "port",
    "ollama_chat": "ollama",
}


async def acquire_lock(action: str, params: dict[str, Any]) -> asyncio.Lock | None:
    """
    Acquire the appropriate resource lock for the given action.

    Returns the Lock object (caller must release it), or None if the
    action doesn't require a lock.
    """
    resolver = ACTION_LOCK_MAP.get(action)
    if resolver is None:
        return None

    if callable(resolver):
        lock_name = resolver(params)
    else:
        lock_name = resolver

    lock = _locks.get(lock_name)
    if lock is None:
        return None

    logger.debug("Acquiring lock '%s' for action '%s'", lock_name, action)
    await lock.acquire()
    logger.debug("Acquired lock '%s'", lock_name)
    return lock


def release_lock(lock: asyncio.Lock | None) -> None:
    """Release a previously acquired lock. Safe to call with None."""
    if lock is not None:
        lock.release()
