"""
SKYNET Archive — Artifact Store

Stores and retrieves job artifacts (output files, screenshots, logs, etc.).

Features:
  - Local filesystem storage
  - S3 storage (optional)
  - Artifact metadata tracking
  - Artifact querying and retrieval
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("skynet.archive.artifacts")


@dataclass
class Artifact:
    """Metadata for a stored artifact."""

    artifact_id: str
    job_id: str
    filename: str
    content_type: str
    size_bytes: int
    storage_path: str
    s3_key: str | None = None
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "job_id": self.job_id,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "storage_path": self.storage_path,
            "s3_key": self.s3_key,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


class ArtifactStore:
    """
    Manages artifact storage for job outputs.

    Stores artifacts both locally and optionally in S3.
    Tracks metadata for querying and retrieval.
    """

    def __init__(
        self,
        local_storage_path: str = "data/artifacts",
        s3_client: Any | None = None,
        s3_bucket: str | None = None,
    ):
        """
        Initialize artifact store.

        Args:
            local_storage_path: Local directory for artifact storage
            s3_client: Optional S3 client for remote storage
            s3_bucket: S3 bucket name (if using S3)
        """
        self.local_storage_path = Path(local_storage_path)
        self.s3_client = s3_client
        self.s3_bucket = s3_bucket
        self._artifacts: dict[str, Artifact] = {}

        # Ensure local storage directory exists
        self.local_storage_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Artifact store initialized: {self.local_storage_path}")

    def _generate_artifact_id(self, job_id: str, filename: str) -> str:
        """Generate unique artifact ID."""
        unique_str = f"{job_id}:{filename}:{time.time()}"
        return hashlib.sha256(unique_str.encode()).hexdigest()[:16]

    def _get_storage_path(self, job_id: str, artifact_id: str, filename: str) -> Path:
        """Get local storage path for artifact."""
        # Organize by job_id for easy cleanup
        job_dir = self.local_storage_path / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir / f"{artifact_id}_{filename}"

    async def store_artifact(
        self,
        job_id: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
        upload_to_s3: bool = False,
    ) -> Artifact:
        """
        Store an artifact.

        Args:
            job_id: Job ID this artifact belongs to
            filename: Original filename
            content: Artifact content (bytes)
            content_type: MIME type
            metadata: Additional metadata
            upload_to_s3: Whether to upload to S3

        Returns:
            Artifact metadata
        """
        artifact_id = self._generate_artifact_id(job_id, filename)
        storage_path = self._get_storage_path(job_id, artifact_id, filename)

        # Write to local storage
        storage_path.write_bytes(content)

        logger.info(
            f"Stored artifact {artifact_id} ({len(content)} bytes): {filename}"
        )

        # Upload to S3 if requested
        s3_key = None
        if upload_to_s3 and self.s3_client and self.s3_bucket:
            s3_key = f"artifacts/{job_id}/{artifact_id}_{filename}"
            try:
                await self._upload_to_s3(s3_key, content, content_type)
                logger.info(f"Uploaded artifact to S3: {s3_key}")
            except Exception as e:
                logger.error(f"Failed to upload artifact to S3: {e}")

        # Create artifact metadata
        artifact = Artifact(
            artifact_id=artifact_id,
            job_id=job_id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(content),
            storage_path=str(storage_path),
            s3_key=s3_key,
            metadata=metadata or {},
        )

        self._artifacts[artifact_id] = artifact

        return artifact

    async def _upload_to_s3(
        self, key: str, content: bytes, content_type: str
    ) -> None:
        """Upload artifact to S3."""
        # This is a stub - implement with actual S3 client
        # For example with aioboto3 or aiohttp to S3 presigned URLs
        if not self.s3_client:
            return

        # Placeholder for actual S3 upload
        # await self.s3_client.put_object(
        #     Bucket=self.s3_bucket,
        #     Key=key,
        #     Body=content,
        #     ContentType=content_type,
        # )
        logger.debug(f"S3 upload stub called for key: {key}")

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        """
        Get artifact metadata.

        Args:
            artifact_id: Artifact ID

        Returns:
            Artifact metadata or None if not found
        """
        return self._artifacts.get(artifact_id)

    async def get_artifact_content(self, artifact_id: str) -> bytes | None:
        """
        Get artifact content.

        Args:
            artifact_id: Artifact ID

        Returns:
            Artifact content or None if not found
        """
        artifact = self._artifacts.get(artifact_id)
        if not artifact:
            return None

        storage_path = Path(artifact.storage_path)
        if not storage_path.exists():
            logger.warning(f"Artifact file not found: {storage_path}")
            return None

        return storage_path.read_bytes()

    async def list_artifacts(
        self,
        job_id: str | None = None,
        limit: int = 100,
    ) -> list[Artifact]:
        """
        List artifacts.

        Args:
            job_id: Filter by job ID (optional)
            limit: Maximum number of artifacts to return

        Returns:
            List of artifact metadata
        """
        artifacts = list(self._artifacts.values())

        # Filter by job_id if specified
        if job_id:
            artifacts = [a for a in artifacts if a.job_id == job_id]

        # Sort by created_at (newest first)
        artifacts.sort(key=lambda a: a.created_at, reverse=True)

        # Apply limit
        return artifacts[:limit]

    async def delete_artifact(self, artifact_id: str) -> bool:
        """
        Delete an artifact.

        Args:
            artifact_id: Artifact ID

        Returns:
            True if deleted, False if not found
        """
        artifact = self._artifacts.get(artifact_id)
        if not artifact:
            return False

        # Delete local file
        storage_path = Path(artifact.storage_path)
        if storage_path.exists():
            storage_path.unlink()

        # Delete from S3 if it was uploaded
        if artifact.s3_key and self.s3_client:
            try:
                await self._delete_from_s3(artifact.s3_key)
            except Exception as e:
                logger.error(f"Failed to delete artifact from S3: {e}")

        # Remove from metadata
        del self._artifacts[artifact_id]

        logger.info(f"Deleted artifact {artifact_id}")
        return True

    async def _delete_from_s3(self, key: str) -> None:
        """Delete artifact from S3."""
        if not self.s3_client:
            return

        # Placeholder for actual S3 deletion
        # await self.s3_client.delete_object(
        #     Bucket=self.s3_bucket,
        #     Key=key,
        # )
        logger.debug(f"S3 delete stub called for key: {key}")

    async def delete_job_artifacts(self, job_id: str) -> int:
        """
        Delete all artifacts for a job.

        Args:
            job_id: Job ID

        Returns:
            Number of artifacts deleted
        """
        artifacts = [a for a in self._artifacts.values() if a.job_id == job_id]

        count = 0
        for artifact in artifacts:
            if await self.delete_artifact(artifact.artifact_id):
                count += 1

        logger.info(f"Deleted {count} artifacts for job {job_id}")
        return count

    async def get_storage_stats(self) -> dict[str, Any]:
        """
        Get storage statistics.

        Returns:
            Dict with storage stats
        """
        total_size = sum(a.size_bytes for a in self._artifacts.values())
        total_count = len(self._artifacts)

        # Count by content type
        by_type: dict[str, int] = {}
        for artifact in self._artifacts.values():
            by_type[artifact.content_type] = by_type.get(artifact.content_type, 0) + 1

        return {
            "total_artifacts": total_count,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "by_content_type": by_type,
            "storage_path": str(self.local_storage_path),
            "s3_enabled": self.s3_client is not None,
        }

    async def cleanup_old_artifacts(self, max_age_days: int = 30) -> int:
        """
        Delete artifacts older than specified age.

        Args:
            max_age_days: Maximum age in days

        Returns:
            Number of artifacts deleted
        """
        cutoff_time = time.time() - (max_age_days * 24 * 60 * 60)

        old_artifacts = [
            a for a in self._artifacts.values() if a.created_at < cutoff_time
        ]

        count = 0
        for artifact in old_artifacts:
            if await self.delete_artifact(artifact.artifact_id):
                count += 1

        logger.info(f"Cleaned up {count} artifacts older than {max_age_days} days")
        return count
