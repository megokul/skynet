"""
Memory Storage Layer — PostgreSQL with SQLite fallback.

Handles persistence of all memory records with support for:
- PostgreSQL + pgvector for production (vector similarity search)
- SQLite for development/testing (no vector search)
- Automatic connection pool management
- Schema migrations
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import MemoryRecord, MemoryType

logger = logging.getLogger("skynet.memory.storage")


# ============================================================================
# Base Storage Interface
# ============================================================================


class MemoryStorage:
    """Base interface for memory storage backends."""

    async def initialize(self) -> None:
        """Initialize storage (create tables, indexes, etc.)."""
        raise NotImplementedError

    async def store_memory(self, memory: MemoryRecord) -> str:
        """Store a memory record. Returns memory ID."""
        raise NotImplementedError

    async def get_memory(self, memory_id: str) -> MemoryRecord | None:
        """Retrieve a specific memory by ID."""
        raise NotImplementedError

    async def search_memories(
        self,
        memory_type: MemoryType | None = None,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        """Search memories with filters."""
        raise NotImplementedError

    async def search_similar(
        self,
        embedding: list[float],
        limit: int = 10,
        memory_type: MemoryType | None = None,
    ) -> list[MemoryRecord]:
        """Vector similarity search (PostgreSQL only)."""
        raise NotImplementedError

    async def update_retrieval_count(self, memory_id: str) -> None:
        """Increment retrieval count for a memory."""
        raise NotImplementedError

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory record."""
        raise NotImplementedError

    async def close(self) -> None:
        """Close database connection."""
        raise NotImplementedError


# ============================================================================
# SQLite Storage (Development/Fallback)
# ============================================================================


class SQLiteMemoryStorage(MemoryStorage):
    """
    SQLite-based memory storage.

    Simpler, no vector search, but works without PostgreSQL.
    """

    def __init__(self, db_path: str = "skynet_memory.db"):
        """Initialize SQLite storage."""
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    async def initialize(self) -> None:
        """Create SQLite tables and indexes."""
        # Ensure directory exists
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        # Connect
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

        # Create schema
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_records (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,  -- JSON
                metadata TEXT NOT NULL DEFAULT '{}',  -- JSON
                embedding TEXT,  -- JSON (no vector search in SQLite)
                retrieval_count INTEGER DEFAULT 0,
                importance_score REAL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_memory_type
                ON memory_records(memory_type);
            CREATE INDEX IF NOT EXISTS idx_memory_timestamp
                ON memory_records(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_memory_importance
                ON memory_records(importance_score DESC);
            CREATE INDEX IF NOT EXISTS idx_memory_retrieval
                ON memory_records(retrieval_count DESC);

            CREATE TABLE IF NOT EXISTS task_history (
                id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                user_message TEXT NOT NULL,
                plan TEXT NOT NULL,  -- JSON
                execution_result TEXT,  -- JSON
                risk_level TEXT,
                duration_seconds INTEGER,
                success INTEGER,  -- 0 or 1 (boolean)
                provider TEXT,
                target TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_task_request
                ON task_history(request_id);
            CREATE INDEX IF NOT EXISTS idx_task_created
                ON task_history(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_task_success
                ON task_history(success);

            CREATE TABLE IF NOT EXISTS system_state (
                id TEXT PRIMARY KEY,
                state_type TEXT NOT NULL,
                state_data TEXT NOT NULL,  -- JSON
                snapshot_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_state_type
                ON system_state(state_type);
            CREATE INDEX IF NOT EXISTS idx_state_snapshot
                ON system_state(snapshot_at DESC);
            """
        )

        self.conn.commit()
        logger.info(f"SQLite memory storage initialized at {self.db_path}")

    async def store_memory(self, memory: MemoryRecord) -> str:
        """Store memory in SQLite."""
        if not self.conn:
            raise RuntimeError("Storage not initialized")

        self.conn.execute(
            """
            INSERT INTO memory_records
                (id, timestamp, memory_type, content, metadata, embedding,
                 retrieval_count, importance_score, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.id,
                memory.timestamp.isoformat(),
                memory.memory_type.value,
                json.dumps(memory.content),
                json.dumps(memory.metadata),
                json.dumps(memory.embedding) if memory.embedding else None,
                memory.retrieval_count,
                memory.importance_score,
                memory.created_at.isoformat(),
                memory.updated_at.isoformat(),
            ),
        )

        self.conn.commit()
        return memory.id

    async def get_memory(self, memory_id: str) -> MemoryRecord | None:
        """Retrieve memory from SQLite."""
        if not self.conn:
            raise RuntimeError("Storage not initialized")

        cursor = self.conn.execute(
            "SELECT * FROM memory_records WHERE id = ?", (memory_id,)
        )

        row = cursor.fetchone()
        if not row:
            return None

        return self._row_to_memory(row)

    async def search_memories(
        self,
        memory_type: MemoryType | None = None,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        """Search memories with filters."""
        if not self.conn:
            raise RuntimeError("Storage not initialized")

        query = "SELECT * FROM memory_records WHERE 1=1"
        params = []

        if memory_type:
            query += " AND memory_type = ?"
            params.append(memory_type.value)

        # Sort by importance score and recency
        query += " ORDER BY importance_score DESC, timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = self.conn.execute(query, params)
        rows = cursor.fetchall()

        return [self._row_to_memory(row) for row in rows]

    async def search_similar(
        self,
        embedding: list[float],
        limit: int = 10,
        memory_type: MemoryType | None = None,
    ) -> list[MemoryRecord]:
        """
        Vector similarity search (NOT SUPPORTED in SQLite).

        Falls back to most recent memories.
        """
        logger.warning("Vector search not supported in SQLite, returning recent memories")
        return await self.search_memories(memory_type=memory_type, limit=limit)

    async def update_retrieval_count(self, memory_id: str) -> None:
        """Increment retrieval count."""
        if not self.conn:
            raise RuntimeError("Storage not initialized")

        self.conn.execute(
            """
            UPDATE memory_records
            SET retrieval_count = retrieval_count + 1,
                updated_at = ?
            WHERE id = ?
            """,
            (datetime.utcnow().isoformat(), memory_id),
        )

        self.conn.commit()

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete memory."""
        if not self.conn:
            raise RuntimeError("Storage not initialized")

        cursor = self.conn.execute(
            "DELETE FROM memory_records WHERE id = ?", (memory_id,)
        )

        self.conn.commit()
        return cursor.rowcount > 0

    async def close(self) -> None:
        """Close SQLite connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def _row_to_memory(self, row: sqlite3.Row) -> MemoryRecord:
        """Convert SQLite row to MemoryRecord."""
        embedding_str = row["embedding"]
        embedding = json.loads(embedding_str) if embedding_str else None

        return MemoryRecord(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            memory_type=MemoryType(row["memory_type"]),
            content=json.loads(row["content"]),
            metadata=json.loads(row["metadata"]),
            embedding=embedding,
            retrieval_count=row["retrieval_count"],
            importance_score=row["importance_score"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


# ============================================================================
# PostgreSQL Storage (Production)
# ============================================================================


class PostgreSQLMemoryStorage(MemoryStorage):
    """
    PostgreSQL-based memory storage with pgvector.

    Full production features:
    - Vector similarity search
    - Better concurrency
    - JSONB for flexible queries
    - Scalability
    """

    def __init__(self, database_url: str):
        """Initialize PostgreSQL storage."""
        self.database_url = database_url
        self.pool: Any = None  # asyncpg connection pool

        try:
            import asyncpg  # noqa: F401

            self._has_asyncpg = True
        except ImportError:
            logger.error(
                "asyncpg not installed. Install with: pip install asyncpg pgvector"
            )
            self._has_asyncpg = False

    async def initialize(self) -> None:
        """Create PostgreSQL tables, indexes, and extensions."""
        if not self._has_asyncpg:
            raise RuntimeError("asyncpg not installed")

        import asyncpg

        # Create connection pool
        self.pool = await asyncpg.create_pool(self.database_url)

        # Create schema
        async with self.pool.acquire() as conn:
            # Enable pgvector extension
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

            # Memory records table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_records (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    memory_type VARCHAR(50) NOT NULL,
                    content JSONB NOT NULL,
                    metadata JSONB DEFAULT '{}',
                    embedding VECTOR(1536),
                    retrieval_count INTEGER DEFAULT 0,
                    importance_score FLOAT DEFAULT 0.0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )

            # Indexes
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_records(memory_type);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_timestamp ON memory_records(timestamp DESC);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_importance ON memory_records(importance_score DESC);"
            )

            # Vector index for similarity search (IVFFlat)
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_embedding
                ON memory_records USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100);
                """
            )

            # Task history table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_history (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    request_id VARCHAR(255) NOT NULL,
                    user_message TEXT NOT NULL,
                    plan JSONB NOT NULL,
                    execution_result JSONB,
                    risk_level VARCHAR(20),
                    duration_seconds INTEGER,
                    success BOOLEAN,
                    provider VARCHAR(50),
                    target VARCHAR(50),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )

            # Task history indexes
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_request ON task_history(request_id);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_created ON task_history(created_at DESC);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_success ON task_history(success);"
            )

            # System state table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_state (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    state_type VARCHAR(50) NOT NULL,
                    state_data JSONB NOT NULL,
                    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )

            # System state indexes
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_state_type ON system_state(state_type);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_state_snapshot ON system_state(snapshot_at DESC);"
            )

        logger.info("PostgreSQL memory storage initialized with pgvector")

    async def store_memory(self, memory: MemoryRecord) -> str:
        """Store memory in PostgreSQL."""
        if not self.pool:
            raise RuntimeError("Storage not initialized")

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memory_records
                    (id, timestamp, memory_type, content, metadata, embedding,
                     retrieval_count, importance_score, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                memory.id,
                memory.timestamp,
                memory.memory_type.value,
                json.dumps(memory.content),
                json.dumps(memory.metadata),
                memory.embedding,
                memory.retrieval_count,
                memory.importance_score,
                memory.created_at,
                memory.updated_at,
            )

        return memory.id

    async def get_memory(self, memory_id: str) -> MemoryRecord | None:
        """Retrieve memory from PostgreSQL."""
        if not self.pool:
            raise RuntimeError("Storage not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM memory_records WHERE id = $1", memory_id
            )

        if not row:
            return None

        return self._row_to_memory(row)

    async def search_memories(
        self,
        memory_type: MemoryType | None = None,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        """Search memories with filters."""
        if not self.pool:
            raise RuntimeError("Storage not initialized")

        query = "SELECT * FROM memory_records WHERE 1=1"
        params = []
        param_count = 1

        if memory_type:
            query += f" AND memory_type = ${param_count}"
            params.append(memory_type.value)
            param_count += 1

        query += f" ORDER BY importance_score DESC, timestamp DESC LIMIT ${param_count}"
        params.append(limit)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [self._row_to_memory(row) for row in rows]

    async def search_similar(
        self,
        embedding: list[float],
        limit: int = 10,
        memory_type: MemoryType | None = None,
    ) -> list[MemoryRecord]:
        """Vector similarity search using pgvector."""
        if not self.pool:
            raise RuntimeError("Storage not initialized")

        query = """
            SELECT *, (embedding <=> $1::vector) as distance
            FROM memory_records
            WHERE embedding IS NOT NULL
        """
        params = [embedding]
        param_count = 2

        if memory_type:
            query += f" AND memory_type = ${param_count}"
            params.append(memory_type.value)
            param_count += 1

        query += f" ORDER BY distance LIMIT ${param_count}"
        params.append(limit)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [self._row_to_memory(row) for row in rows]

    async def update_retrieval_count(self, memory_id: str) -> None:
        """Increment retrieval count."""
        if not self.pool:
            raise RuntimeError("Storage not initialized")

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE memory_records
                SET retrieval_count = retrieval_count + 1,
                    updated_at = NOW()
                WHERE id = $1
                """,
                memory_id,
            )

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete memory."""
        if not self.pool:
            raise RuntimeError("Storage not initialized")

        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM memory_records WHERE id = $1", memory_id
            )

        return result == "DELETE 1"

    async def close(self) -> None:
        """Close PostgreSQL connection pool."""
        if self.pool:
            await self.pool.close()
            self.pool = None

    def _row_to_memory(self, row: Any) -> MemoryRecord:
        """Convert PostgreSQL row to MemoryRecord."""
        return MemoryRecord(
            id=str(row["id"]),
            timestamp=row["timestamp"],
            memory_type=MemoryType(row["memory_type"]),
            content=json.loads(row["content"]) if isinstance(row["content"], str) else row["content"],
            metadata=json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"],
            embedding=row["embedding"],
            retrieval_count=row["retrieval_count"],
            importance_score=row["importance_score"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ============================================================================
# Storage Factory
# ============================================================================


def create_memory_storage(database_url: str | None = None) -> MemoryStorage:
    """
    Create appropriate storage backend based on database URL.

    Args:
        database_url: PostgreSQL connection string or None for SQLite

    Returns:
        MemoryStorage instance (PostgreSQL or SQLite)
    """
    url = database_url or os.getenv("DATABASE_URL")

    if url and url.startswith("postgresql://"):
        logger.info("Using PostgreSQL memory storage")
        return PostgreSQLMemoryStorage(url)
    else:
        # SQLite fallback
        db_path = os.getenv("SKYNET_MEMORY_DB", "data/skynet_memory.db")
        logger.info(f"Using SQLite memory storage at {db_path}")
        return SQLiteMemoryStorage(db_path)
