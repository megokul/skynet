"""
Test ArtifactStore — Artifact Storage and Retrieval

Tests the ArtifactStore that manages job output artifacts.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.archive.artifact_store import ArtifactStore


async def test_artifact_store_initialization():
    """Test ArtifactStore initialization."""
    print("\n[TEST 1] ArtifactStore initialization")

    store = ArtifactStore(local_storage_path="data/test_artifacts")
    assert store.local_storage_path.exists()
    assert store._artifacts == {}
    print("  [PASS] Store initialized")


async def test_store_artifact():
    """Test storing an artifact."""
    print("\n[TEST 2] Store artifact")

    store = ArtifactStore(local_storage_path="data/test_artifacts")

    content = b"Hello, world!"
    artifact = await store.store_artifact(
        job_id="job-123",
        filename="output.txt",
        content=content,
        content_type="text/plain",
        metadata={"source": "test"},
    )

    assert artifact.job_id == "job-123"
    assert artifact.filename == "output.txt"
    assert artifact.size_bytes == len(content)
    assert artifact.content_type == "text/plain"
    assert artifact.metadata["source"] == "test"
    assert Path(artifact.storage_path).exists()
    print("  [PASS] Artifact stored")


async def test_get_artifact():
    """Test retrieving artifact metadata."""
    print("\n[TEST 3] Get artifact metadata")

    store = ArtifactStore(local_storage_path="data/test_artifacts")

    # Store artifact
    content = b"Test content"
    stored = await store.store_artifact(
        job_id="job-456",
        filename="data.json",
        content=content,
    )

    # Retrieve metadata
    artifact = await store.get_artifact(stored.artifact_id)
    assert artifact is not None
    assert artifact.artifact_id == stored.artifact_id
    assert artifact.job_id == "job-456"
    print("  [PASS] Artifact metadata retrieved")


async def test_get_artifact_content():
    """Test retrieving artifact content."""
    print("\n[TEST 4] Get artifact content")

    store = ArtifactStore(local_storage_path="data/test_artifacts")

    # Store artifact
    content = b"Test file content"
    stored = await store.store_artifact(
        job_id="job-789",
        filename="file.bin",
        content=content,
    )

    # Retrieve content
    retrieved = await store.get_artifact_content(stored.artifact_id)
    assert retrieved == content
    print("  [PASS] Artifact content retrieved")


async def test_list_artifacts():
    """Test listing artifacts."""
    print("\n[TEST 5] List artifacts")

    store = ArtifactStore(local_storage_path="data/test_artifacts")

    # Store multiple artifacts
    await store.store_artifact("job-1", "file1.txt", b"content1")
    await store.store_artifact("job-1", "file2.txt", b"content2")
    await store.store_artifact("job-2", "file3.txt", b"content3")

    # List all artifacts
    all_artifacts = await store.list_artifacts()
    assert len(all_artifacts) >= 3

    # List artifacts for specific job
    job1_artifacts = await store.list_artifacts(job_id="job-1")
    assert len([a for a in job1_artifacts if a.job_id == "job-1"]) >= 2
    print("  [PASS] Artifacts listed")


async def test_delete_artifact():
    """Test deleting an artifact."""
    print("\n[TEST 6] Delete artifact")

    store = ArtifactStore(local_storage_path="data/test_artifacts")

    # Store artifact
    artifact = await store.store_artifact(
        job_id="job-delete",
        filename="delete_me.txt",
        content=b"temporary",
    )

    # Verify it exists
    assert Path(artifact.storage_path).exists()

    # Delete it
    result = await store.delete_artifact(artifact.artifact_id)
    assert result == True
    assert not Path(artifact.storage_path).exists()

    # Verify it's gone
    retrieved = await store.get_artifact(artifact.artifact_id)
    assert retrieved is None
    print("  [PASS] Artifact deleted")


async def test_delete_job_artifacts():
    """Test deleting all artifacts for a job."""
    print("\n[TEST 7] Delete job artifacts")

    store = ArtifactStore(local_storage_path="data/test_artifacts")

    # Store multiple artifacts for same job
    await store.store_artifact("job-cleanup", "file1.txt", b"content1")
    await store.store_artifact("job-cleanup", "file2.txt", b"content2")
    await store.store_artifact("job-cleanup", "file3.txt", b"content3")

    # Delete all artifacts for job
    count = await store.delete_job_artifacts("job-cleanup")
    assert count == 3

    # Verify they're gone
    remaining = await store.list_artifacts(job_id="job-cleanup")
    assert len(remaining) == 0
    print("  [PASS] Job artifacts deleted")


async def test_storage_stats():
    """Test storage statistics."""
    print("\n[TEST 8] Storage statistics")

    store = ArtifactStore(local_storage_path="data/test_artifacts")

    # Store some artifacts
    await store.store_artifact("job-stats", "file1.txt", b"a" * 1000, content_type="text/plain")
    await store.store_artifact("job-stats", "file2.json", b"b" * 2000, content_type="application/json")

    # Get stats
    stats = await store.get_storage_stats()

    assert "total_artifacts" in stats
    assert "total_size_bytes" in stats
    assert "by_content_type" in stats
    assert stats["total_artifacts"] >= 2
    assert stats["total_size_bytes"] >= 3000
    print("  [PASS] Storage stats retrieved")


async def test_artifact_metadata():
    """Test artifact metadata storage."""
    print("\n[TEST 9] Artifact metadata")

    store = ArtifactStore(local_storage_path="data/test_artifacts")

    metadata = {
        "action": "screenshot",
        "timestamp": "2026-02-16T10:00:00",
        "user": "test_user",
    }

    artifact = await store.store_artifact(
        job_id="job-meta",
        filename="screenshot.png",
        content=b"fake_png_data",
        content_type="image/png",
        metadata=metadata,
    )

    assert artifact.metadata["action"] == "screenshot"
    assert artifact.metadata["user"] == "test_user"
    print("  [PASS] Metadata stored correctly")


async def test_cleanup_old_artifacts():
    """Test cleanup of old artifacts."""
    print("\n[TEST 10] Cleanup old artifacts")

    store = ArtifactStore(local_storage_path="data/test_artifacts")

    # Store artifact and manually modify created_at to make it "old"
    artifact = await store.store_artifact(
        job_id="job-old",
        filename="old_file.txt",
        content=b"old content",
    )

    # Make it appear old (31 days ago)
    import time
    old_time = time.time() - (31 * 24 * 60 * 60)
    artifact.created_at = old_time
    store._artifacts[artifact.artifact_id] = artifact

    # Cleanup artifacts older than 30 days
    count = await store.cleanup_old_artifacts(max_age_days=30)
    assert count >= 1

    # Verify it's gone
    remaining = await store.get_artifact(artifact.artifact_id)
    assert remaining is None
    print("  [PASS] Old artifacts cleaned up")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("ArtifactStore Tests")
    print("=" * 60)

    async def run_async_tests():
        try:
            await test_artifact_store_initialization()
            await test_store_artifact()
            await test_get_artifact()
            await test_get_artifact_content()
            await test_list_artifacts()
            await test_delete_artifact()
            await test_delete_job_artifacts()
            await test_storage_stats()
            await test_artifact_metadata()
            await test_cleanup_old_artifacts()

            print("\n" + "=" * 60)
            print("[SUCCESS] All ArtifactStore tests passed!")
            print("=" * 60)

            return True

        except AssertionError as e:
            print(f"\n[FAILED] Test assertion failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        except Exception as e:
            print(f"\n[ERROR] Test error: {e}")
            import traceback
            traceback.print_exc()
            return False

    return asyncio.run(run_async_tests())


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
