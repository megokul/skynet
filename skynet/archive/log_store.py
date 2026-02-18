"""
SKYNET Archive — Log Store

Stores and queries execution logs for jobs.

Features:
  - Structured log storage
  - Log querying (by job, time range, level)
  - Log tailing
  - Log search
  - Log retention and cleanup
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("skynet.archive.logs")


@dataclass
class LogEntry:
    """A single log entry."""

    job_id: str
    timestamp: float
    level: str  # debug | info | warning | error | critical
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        """Serialize to JSON line."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, line: str) -> LogEntry:
        """Deserialize from JSON line."""
        data = json.loads(line)
        return cls(
            job_id=data["job_id"],
            timestamp=data["timestamp"],
            level=data["level"],
            message=data["message"],
            metadata=data.get("metadata", {}),
        )


class LogStore:
    """
    Manages execution log storage and querying.

    Stores logs as JSON lines for easy parsing and querying.
    Supports filtering, tailing, and search.
    """

    def __init__(self, log_storage_path: str = "data/logs"):
        """
        Initialize log store.

        Args:
            log_storage_path: Directory for log file storage
        """
        self.log_storage_path = Path(log_storage_path)
        self.log_storage_path.mkdir(parents=True, exist_ok=True)

        # In-memory index for recent logs
        self._recent_logs: list[LogEntry] = []
        self._max_recent = 1000  # Keep last 1000 entries in memory

        logger.info(f"Log store initialized: {self.log_storage_path}")

    def _get_log_file_path(self, job_id: str) -> Path:
        """Get log file path for a job."""
        # Organize by job_id
        return self.log_storage_path / f"{job_id}.jsonl"

    async def log(
        self,
        job_id: str,
        level: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> LogEntry:
        """
        Write a log entry.

        Args:
            job_id: Job ID
            level: Log level (debug, info, warning, error, critical)
            message: Log message
            metadata: Additional metadata

        Returns:
            LogEntry
        """
        entry = LogEntry(
            job_id=job_id,
            timestamp=time.time(),
            level=level.lower(),
            message=message,
            metadata=metadata or {},
        )

        # Write to job log file
        log_file = self._get_log_file_path(job_id)
        with log_file.open("a", encoding="utf-8") as f:
            f.write(entry.to_json() + "\n")

        # Add to recent logs
        self._recent_logs.append(entry)
        if len(self._recent_logs) > self._max_recent:
            self._recent_logs = self._recent_logs[-self._max_recent :]

        return entry

    async def get_job_logs(
        self,
        job_id: str,
        level: str | None = None,
        limit: int | None = None,
    ) -> list[LogEntry]:
        """
        Get logs for a specific job.

        Args:
            job_id: Job ID
            level: Filter by log level (optional)
            limit: Maximum number of entries to return (optional)

        Returns:
            List of log entries
        """
        log_file = self._get_log_file_path(job_id)
        if not log_file.exists():
            return []

        logs: list[LogEntry] = []

        with log_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    entry = LogEntry.from_json(line)

                    # Filter by level if specified
                    if level and entry.level != level.lower():
                        continue

                    logs.append(entry)

                except Exception as e:
                    logger.warning(f"Failed to parse log line: {e}")
                    continue

        # Apply limit if specified (take last N entries)
        if limit and len(logs) > limit:
            logs = logs[-limit:]

        return logs

    async def tail_job_logs(
        self, job_id: str, num_lines: int = 50
    ) -> list[LogEntry]:
        """
        Get last N log entries for a job (tail).

        Args:
            job_id: Job ID
            num_lines: Number of lines to return

        Returns:
            List of last N log entries
        """
        return await self.get_job_logs(job_id, limit=num_lines)

    async def search_logs(
        self,
        query: str,
        job_id: str | None = None,
        level: str | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        """
        Search logs by text.

        Args:
            query: Search query (case-insensitive substring match)
            job_id: Filter by job ID (optional)
            level: Filter by log level (optional)
            limit: Maximum number of results

        Returns:
            List of matching log entries
        """
        query_lower = query.lower()
        results: list[LogEntry] = []

        # If job_id specified, search only that job's logs
        if job_id:
            log_files = [self._get_log_file_path(job_id)]
        else:
            # Search all log files
            log_files = list(self.log_storage_path.glob("*.jsonl"))

        for log_file in log_files:
            if not log_file.exists():
                continue

            with log_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        entry = LogEntry.from_json(line)

                        # Filter by level if specified
                        if level and entry.level != level.lower():
                            continue

                        # Search in message
                        if query_lower in entry.message.lower():
                            results.append(entry)

                            # Stop if we've reached the limit
                            if len(results) >= limit:
                                return results

                    except Exception as e:
                        logger.warning(f"Failed to parse log line: {e}")
                        continue

        return results

    async def get_recent_logs(
        self,
        level: str | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        """
        Get recent logs from in-memory cache.

        Args:
            level: Filter by log level (optional)
            limit: Maximum number of entries

        Returns:
            List of recent log entries
        """
        logs = self._recent_logs

        # Filter by level if specified
        if level:
            logs = [log for log in logs if log.level == level.lower()]

        # Sort by timestamp (newest first)
        logs = sorted(logs, key=lambda x: x.timestamp, reverse=True)

        # Apply limit
        return logs[:limit]

    async def delete_job_logs(self, job_id: str) -> bool:
        """
        Delete all logs for a job.

        Args:
            job_id: Job ID

        Returns:
            True if deleted, False if not found
        """
        log_file = self._get_log_file_path(job_id)
        if not log_file.exists():
            return False

        log_file.unlink()

        # Remove from recent logs
        self._recent_logs = [log for log in self._recent_logs if log.job_id != job_id]

        logger.info(f"Deleted logs for job {job_id}")
        return True

    async def cleanup_old_logs(self, max_age_days: int = 30) -> int:
        """
        Delete log files older than specified age.

        Args:
            max_age_days: Maximum age in days

        Returns:
            Number of log files deleted
        """
        cutoff_time = time.time() - (max_age_days * 24 * 60 * 60)

        count = 0
        for log_file in self.log_storage_path.glob("*.jsonl"):
            # Check file modification time
            if log_file.stat().st_mtime < cutoff_time:
                job_id = log_file.stem  # Filename without extension
                if await self.delete_job_logs(job_id):
                    count += 1

        logger.info(f"Cleaned up {count} log files older than {max_age_days} days")
        return count

    async def get_log_stats(self) -> dict[str, Any]:
        """
        Get log storage statistics.

        Returns:
            Dict with log stats
        """
        log_files = list(self.log_storage_path.glob("*.jsonl"))

        total_size = sum(f.stat().st_size for f in log_files)
        total_jobs = len(log_files)

        # Count by level from recent logs
        by_level: dict[str, int] = {}
        for entry in self._recent_logs:
            by_level[entry.level] = by_level.get(entry.level, 0) + 1

        return {
            "total_log_files": total_jobs,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "recent_logs_count": len(self._recent_logs),
            "recent_by_level": by_level,
            "storage_path": str(self.log_storage_path),
        }

    async def format_logs(
        self, logs: list[LogEntry], include_metadata: bool = False
    ) -> str:
        """
        Format log entries for display.

        Args:
            logs: List of log entries
            include_metadata: Whether to include metadata

        Returns:
            Formatted log output
        """
        lines: list[str] = []

        for log in logs:
            # Format timestamp
            import datetime

            dt = datetime.datetime.fromtimestamp(log.timestamp)
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")

            # Format level with color indicators
            level_icons = {
                "debug": "[D]",
                "info": "[I]",
                "warning": "[W]",
                "error": "[E]",
                "critical": "[!]",
            }
            level_icon = level_icons.get(log.level, "[?]")

            # Build log line
            line = f"{time_str} {level_icon} {log.job_id[:8]}: {log.message}"

            # Add metadata if requested
            if include_metadata and log.metadata:
                line += f" {json.dumps(log.metadata)}"

            lines.append(line)

        return "\n".join(lines)
