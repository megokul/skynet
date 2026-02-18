"""
SKYNET — S3 Storage Client

Handles durable storage of project artifacts, conversation archives,
provider usage snapshots, and model output caching on AWS S3.

Design constraints (AWS free tier):
  - 5 GB storage
  - 20,000 GET requests / month
  - 2,000 PUT requests / month
  - 1 GB outbound transfer / month

All uploads are gzip-compressed to minimize storage and transfer.
Batch operations preferred over per-action uploads.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
from datetime import datetime, timezone
from functools import partial
from typing import Any

logger = logging.getLogger("skynet.storage.s3")


class S3Storage:
    """Async-friendly S3 client for SKYNET artifact storage."""

    def __init__(self, bucket: str, prefix: str = "openclaw/", region: str = "us-east-1"):
        self.bucket = bucket
        self.prefix = prefix
        self.region = region
        self._client = None
        self._loop = None

    def _get_client(self):
        """Lazy-init boto3 S3 client (not async-safe, call from executor)."""
        if self._client is None:
            import boto3
            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    async def _run_sync(self, func, *args, **kwargs):
        """Run a synchronous boto3 call in a thread executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    def _full_key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """Upload raw bytes to S3 (gzip-compressed)."""
        compressed = gzip.compress(data)
        full_key = self._full_key(key)

        def _put():
            client = self._get_client()
            client.put_object(
                Bucket=self.bucket,
                Key=full_key,
                Body=compressed,
                ContentType=content_type,
                ContentEncoding="gzip",
            )

        await self._run_sync(_put)
        logger.info("Uploaded %s (%d bytes compressed)", full_key, len(compressed))

    async def download(self, key: str) -> bytes | None:
        """Download and decompress an object from S3. Returns None if not found."""
        full_key = self._full_key(key)

        def _get():
            client = self._get_client()
            try:
                resp = client.get_object(Bucket=self.bucket, Key=full_key)
                body = resp["Body"].read()
                try:
                    return gzip.decompress(body)
                except gzip.BadGzipFile:
                    return body  # Not compressed.
            except client.exceptions.NoSuchKey:
                return None

        return await self._run_sync(_get)

    async def exists(self, key: str) -> bool:
        """Check if an object exists in S3."""
        full_key = self._full_key(key)

        def _head():
            client = self._get_client()
            try:
                client.head_object(Bucket=self.bucket, Key=full_key)
                return True
            except Exception:
                return False

        return await self._run_sync(_head)

    # ------------------------------------------------------------------
    # Project artifacts
    # ------------------------------------------------------------------

    async def upload_project_snapshot(self, project_slug: str, version: int, zip_bytes: bytes) -> str:
        """Upload a project zip snapshot. Returns the S3 key."""
        key = f"projects/{project_slug}/v{version}/snapshot.zip"
        await self.upload(key, zip_bytes, content_type="application/zip")
        return key

    # ------------------------------------------------------------------
    # Conversation archive
    # ------------------------------------------------------------------

    async def archive_conversations(self, project_id: str, messages: list[dict]) -> str:
        """Archive conversation messages to S3. Returns the S3 key."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        key = f"conversations/{project_id}/{ts}.json"
        data = json.dumps(messages, default=str).encode()
        await self.upload(key, data, content_type="application/json")
        return key

    # ------------------------------------------------------------------
    # Provider usage snapshots
    # ------------------------------------------------------------------

    async def snapshot_provider_usage(self, usage_data: list[dict]) -> str:
        """Save daily provider usage snapshot. Returns the S3 key."""
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"usage/provider_usage_{date}.json"
        data = json.dumps(usage_data, default=str).encode()
        await self.upload(key, data, content_type="application/json")
        return key

    # ------------------------------------------------------------------
    # Model output cache
    # ------------------------------------------------------------------

    @staticmethod
    def _cache_hash(model: str, system: str, messages: list[dict]) -> str:
        """Generate a deterministic hash for caching a prompt."""
        payload = json.dumps({"model": model, "system": system, "messages": messages}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    async def cache_response(self, prompt_hash: str, response: dict) -> None:
        """Store a cached AI response."""
        key = f"cache/{prompt_hash}.json"
        data = json.dumps(response, default=str).encode()
        await self.upload(key, data, content_type="application/json")

    async def get_cached_response(self, prompt_hash: str) -> dict | None:
        """Retrieve a cached AI response. Returns None on miss."""
        key = f"cache/{prompt_hash}.json"
        data = await self.download(key)
        if data is None:
            return None
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    async def list_keys(self, prefix: str, max_keys: int = 100) -> list[str]:
        """List object keys under a prefix."""
        full_prefix = self._full_key(prefix)

        def _list():
            client = self._get_client()
            resp = client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=full_prefix,
                MaxKeys=max_keys,
            )
            return [obj["Key"] for obj in resp.get("Contents", [])]

        return await self._run_sync(_list)
