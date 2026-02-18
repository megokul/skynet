"""
SKYNET — Memory Manager

Persistent per-agent memory stored as .md files on the laptop
(via the agent's file_write/file_read actions) and backed up to S3.

Memory file structure on the laptop:
    {project_path}/.openclaw/
        memory.md               — shared project memory
        agents.md               — agent registry
        agents/{role}/
            identity.md         — agent persona + capabilities
            memory.md           — personal observations + task history
            heartbeat.md        — last activity + status
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite

logger = logging.getLogger("skynet.archive")


class MemoryManager:
    """Manages persistent agent memory files and S3 sync."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        gateway_api_url: str,
        s3: Any | None = None,
    ):
        self.db = db
        self.gateway_api_url = gateway_api_url
        self.s3 = s3

    async def initialize_agent_memory(
        self,
        agent_id: str,
        role: str,
        project: dict,
        config: dict,
    ) -> None:
        """Create initial memory files for an agent on the laptop."""
        project_path = project["local_path"]
        base = f"{project_path}\\.openclaw"
        agent_dir = f"{base}\\agents\\{role}"

        # Create directories.
        await self._agent_action("create_directory", {"directory": base})
        await self._agent_action("create_directory", {"directory": f"{base}\\agents"})
        await self._agent_action("create_directory", {"directory": agent_dir})

        # identity.md — agent persona.
        identity = (
            f"# {config.get('display_name', role.title() + ' Agent')}\n\n"
            f"**Role**: {role}\n"
            f"**Agent ID**: {agent_id}\n"
            f"**Project**: {project['display_name']}\n\n"
            f"## Description\n{config.get('description', '')}\n\n"
            f"## Skills\n"
            + "\n".join(f"- {s}" for s in config.get("skills", []))
            + "\n\n"
            f"## Preferred Providers\n"
            + "\n".join(f"- {p}" for p in config.get("preferred_providers", []))
            + "\n"
        )
        await self._write_if_missing(f"{agent_dir}\\identity.md", identity)

        # memory.md — starts empty.
        await self._write_if_missing(
            f"{agent_dir}\\memory.md",
            f"# {config.get('display_name', role)} Memory\n\n"
            f"_Task history and observations for {project['display_name']}_\n\n"
            "---\n\n",
        )

        # heartbeat.md — initial status.
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        await self._write_file(
            f"{agent_dir}\\heartbeat.md",
            f"# Heartbeat\n\n"
            f"**Status**: idle\n"
            f"**Last active**: {now}\n"
            f"**Tasks completed**: 0\n",
        )

        # Shared project-level files (only first agent writes them).
        await self._write_if_missing(
            f"{base}\\memory.md",
            f"# Project Memory — {project['display_name']}\n\n"
            f"_Shared notes and decisions across all agents._\n\n---\n\n",
        )
        await self._write_if_missing(
            f"{base}\\agents.md",
            f"# Agent Registry — {project['display_name']}\n\n"
            f"| Role | Agent ID | Status |\n"
            f"|------|----------|--------|\n",
        )

        # Append this agent to agents.md.
        agents_md = await self._read_file(f"{base}\\agents.md")
        if agents_md and agent_id not in agents_md:
            agents_md += f"| {role} | {agent_id} | idle |\n"
            await self._write_file(f"{base}\\agents.md", agents_md)

        logger.info("Initialized memory for %s agent %s", role, agent_id[:8])

    async def get_context_for_agent(
        self,
        agent_id: str,
        project_id: str,
    ) -> str:
        """Read an agent's memory.md and return it for context injection."""
        from db import store
        agent = await store.get_agent(self.db, agent_id)
        if not agent:
            return ""

        project = await store.get_project(self.db, project_id)
        if not project or not project.get("local_path"):
            return ""

        role = agent["role"]
        memory_path = f"{project['local_path']}\\.openclaw\\agents\\{role}\\memory.md"

        content = await self._read_file(memory_path)
        if not content:
            return ""

        # Truncate if too long (keep last ~2000 chars).
        if len(content) > 3000:
            content = "...(earlier entries truncated)...\n\n" + content[-2000:]

        return content

    async def update_from_task(
        self,
        agent_id: str,
        project_id: str,
        task: dict,
        result_summary: str,
    ) -> None:
        """Append task completion to the agent's memory and heartbeat."""
        from db import store
        agent = await store.get_agent(self.db, agent_id)
        if not agent:
            return

        project = await store.get_project(self.db, project_id)
        if not project or not project.get("local_path"):
            return

        role = agent["role"]
        base = f"{project['local_path']}\\.openclaw\\agents\\{role}"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        # Append to memory.md.
        memory_path = f"{base}\\memory.md"
        existing = await self._read_file(memory_path) or ""
        summary_short = result_summary[:300] if result_summary else "(no summary)"
        entry = (
            f"## [{now}] {task.get('title', 'Unknown task')}\n\n"
            f"**Milestone**: {task.get('milestone', 'N/A')}\n"
            f"**Result**: {summary_short}\n\n---\n\n"
        )
        await self._write_file(memory_path, existing + entry)

        # Update heartbeat.md.
        tasks_completed = agent.get("tasks_completed", 0) + 1
        await self._write_file(
            f"{base}\\heartbeat.md",
            f"# Heartbeat\n\n"
            f"**Status**: idle\n"
            f"**Last active**: {now}\n"
            f"**Tasks completed**: {tasks_completed}\n"
            f"**Last task**: {task.get('title', '')}\n",
        )

    async def sync_to_s3(self, project_id: str) -> None:
        """Bundle all .openclaw memory files and upload to S3."""
        if not self.s3:
            logger.debug("No S3 client configured, skipping memory sync")
            return

        from db import store
        project = await store.get_project(self.db, project_id)
        if not project or not project.get("local_path"):
            return

        base_dir = f"{project['local_path']}\\.openclaw"

        # Read all known memory files via the agent.
        files_to_read = ["memory.md", "agents.md"]

        # Discover agent subdirectories via list_directory.
        agents_listing = await self._list_dir(f"{base_dir}\\agents")
        if agents_listing:
            for line in agents_listing.strip().split("\n"):
                name = line.strip().replace("[DIR] ", "")
                if name:
                    for fname in ("identity.md", "memory.md", "heartbeat.md"):
                        files_to_read.append(f"agents/{name}/{fname}")

        bundle: dict[str, str] = {}
        for rel_path in files_to_read:
            content = await self._read_file(f"{base_dir}\\{rel_path}")
            if content:
                bundle[rel_path] = content

        if not bundle:
            logger.debug("No memory files to sync for project %s", project_id)
            return

        data = json.dumps(bundle, indent=2).encode()
        slug = project.get("name", project_id)
        key = f"memory/{slug}/memory_bundle.json"
        await self.s3.upload(key, data, content_type="application/json")
        logger.info("Synced memory to S3 for project %s (%d files)", project_id, len(bundle))

    # ------------------------------------------------------------------
    # Agent-side file operations (via HTTP API → WebSocket → laptop)
    # ------------------------------------------------------------------

    async def _agent_action(self, action: str, params: dict) -> str | None:
        """Send an action to the laptop agent via the gateway HTTP API."""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.gateway_api_url}/action",
                    json={"action": action, "params": params, "confirmed": True},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    result = await resp.json()
                    if result.get("status") == "ok":
                        return result.get("result", "")
                    logger.warning("Agent action %s failed: %s", action, result)
                    return None
        except Exception as exc:
            logger.warning("Agent action %s error: %s", action, exc)
            return None

    async def _write_file(self, path: str, content: str) -> None:
        await self._agent_action("file_write", {"file": path, "content": content})

    async def _write_if_missing(self, path: str, content: str) -> None:
        """Write file only if it doesn't already exist."""
        existing = await self._read_file(path)
        if not existing:
            await self._write_file(path, content)

    async def _read_file(self, path: str) -> str | None:
        result = await self._agent_action("file_read", {"file": path})
        if result and not result.startswith("Error"):
            return result
        return None

    async def _list_dir(self, path: str) -> str | None:
        return await self._agent_action("list_directory", {"directory": path})
