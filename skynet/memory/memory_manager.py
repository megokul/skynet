"""
Memory Manager — High-level API for SKYNET's cognitive memory.

Provides:
- Task execution storage and retrieval
- Failure pattern tracking
- Success strategy learning
- Memory importance scoring (recency, success, relevance, frequency)
- Intelligent memory retrieval with ranking
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

from .models import (
    FailurePattern,
    ImportanceScore,
    MemoryRecord,
    MemoryType,
    SuccessStrategy,
    SystemStateSnapshot,
    TaskMemory,
)
from .storage import MemoryStorage, create_memory_storage

logger = logging.getLogger("skynet.memory.manager")


class MemoryManager:
    """
    High-level memory management API.

    Handles storage, retrieval, and scoring of all memory types.
    """

    def __init__(self, storage: MemoryStorage | None = None, vector_indexer=None):
        """
        Initialize MemoryManager.

        Args:
            storage: Memory storage backend (auto-created if None)
            vector_indexer: Vector embedding generator (optional)
        """
        self.storage = storage or create_memory_storage()
        self.vector_indexer = vector_indexer
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize storage and vector indexer."""
        if not self._initialized:
            await self.storage.initialize()
            self._initialized = True
            logger.info("MemoryManager initialized")

    async def close(self) -> None:
        """Close storage connections."""
        if self._initialized:
            await self.storage.close()
            self._initialized = False

    # ========================================================================
    # Task Execution Memory
    # ========================================================================

    async def store_task_execution(
        self,
        request_id: str,
        user_message: str,
        plan: dict[str, Any],
        result: dict[str, Any] | None = None,
        risk_level: str = "LOW",
        duration_seconds: int = 0,
        success: bool = False,
        provider: str = "unknown",
        target: str = "unknown",
        error_message: str | None = None,
    ) -> str:
        """
        Store a task execution memory.

        Args:
            request_id: Unique request identifier
            user_message: User's original task request
            plan: Generated execution plan
            result: Execution result
            risk_level: Task risk level (LOW/MEDIUM/HIGH)
            duration_seconds: Execution duration
            success: Whether task succeeded
            provider: Execution provider used
            target: Execution target
            error_message: Error if failed

        Returns:
            Memory ID
        """
        task_memory = TaskMemory(
            request_id=request_id,
            user_message=user_message,
            plan=plan,
            execution_result=result,
            risk_level=risk_level,
            duration_seconds=duration_seconds,
            success=success,
            provider=provider,
            target=target,
            error_message=error_message,
        )

        memory_record = task_memory.to_memory_record()

        # Generate embedding for semantic search
        if self.vector_indexer:
            try:
                embedding = await self.vector_indexer.generate_embedding(user_message)
                memory_record.embedding = embedding
            except Exception as e:
                logger.warning(f"Failed to generate embedding: {e}")

        memory_id = await self.storage.store_memory(memory_record)
        logger.info(f"Stored task execution memory: {memory_id} (success={success})")

        return memory_id

    async def store_failure_pattern(
        self,
        error_type: str,
        error_message: str,
        context: dict[str, Any],
        suggested_fix: str | None = None,
    ) -> str:
        """
        Store a failure pattern for learning.

        Args:
            error_type: Type of error (e.g., "TimeoutError", "PermissionDenied")
            error_message: Error message
            context: Context when error occurred
            suggested_fix: Suggested fix (if known)

        Returns:
            Memory ID
        """
        failure = FailurePattern(
            error_type=error_type,
            error_message=error_message,
            context=context,
            suggested_fix=suggested_fix,
        )

        memory_record = failure.to_memory_record()
        memory_id = await self.storage.store_memory(memory_record)

        logger.info(f"Stored failure pattern: {error_type}")
        return memory_id

    async def store_success_strategy(
        self,
        strategy_name: str,
        description: str,
        context: dict[str, Any],
        success_rate: float = 1.0,
        applicable_to: list[str] | None = None,
    ) -> str:
        """
        Store a proven successful strategy.

        Args:
            strategy_name: Strategy identifier
            description: Strategy description
            context: Context where strategy works
            success_rate: Historical success rate
            applicable_to: Task types this applies to

        Returns:
            Memory ID
        """
        strategy = SuccessStrategy(
            strategy_name=strategy_name,
            description=description,
            context=context,
            success_rate=success_rate,
            applicable_to=applicable_to or [],
        )

        memory_record = strategy.to_memory_record()
        memory_id = await self.storage.store_memory(memory_record)

        logger.info(f"Stored success strategy: {strategy_name}")
        return memory_id

    async def store_system_state(
        self, state_type: str, state_data: dict[str, Any]
    ) -> str:
        """
        Store a system state snapshot.

        Args:
            state_type: Type of state (provider_health, worker_status, etc.)
            state_data: State data

        Returns:
            Memory ID
        """
        snapshot = SystemStateSnapshot(
            state_type=state_type, state_data=state_data
        )

        memory_record = snapshot.to_memory_record()
        memory_id = await self.storage.store_memory(memory_record)

        return memory_id

    # ========================================================================
    # Memory Retrieval with Importance Scoring
    # ========================================================================

    async def get_relevant_memories(
        self,
        task_context: dict[str, Any],
        limit: int = 5,
        memory_type: MemoryType | None = None,
    ) -> list[tuple[MemoryRecord, ImportanceScore]]:
        """
        Retrieve most relevant memories for a task using importance scoring.

        This is the CORE retrieval method that uses:
        - Recency scoring (exponential decay)
        - Success scoring (successful tasks weighted higher)
        - Relevance scoring (semantic similarity)
        - Frequency scoring (often-retrieved memories gain importance)

        Args:
            task_context: Context including task description and embedding
            limit: Maximum memories to return
            memory_type: Filter by memory type (optional)

        Returns:
            List of (MemoryRecord, ImportanceScore) tuples, sorted by importance
        """
        # Step 1: Get candidates via vector similarity search (if embedding available)
        candidates: list[MemoryRecord] = []

        if "embedding" in task_context and task_context["embedding"]:
            try:
                # Oversample for better filtering
                candidates = await self.storage.search_similar(
                    embedding=task_context["embedding"],
                    limit=limit * 3,
                    memory_type=memory_type,
                )
            except Exception as e:
                logger.warning(f"Vector search failed: {e}, falling back to recency")

        # Fallback: get by recency if no candidates
        if not candidates:
            candidates = await self.storage.search_memories(
                memory_type=memory_type, limit=limit * 3
            )

        # Step 2: Score all candidates
        scored_memories: list[tuple[MemoryRecord, ImportanceScore]] = []

        for memory in candidates:
            score = await self._calculate_importance_score(memory, task_context)
            scored_memories.append((memory, score))

        # Step 3: Sort by total importance score (descending)
        scored_memories.sort(reverse=True, key=lambda x: x[1].total_score)

        # Step 4: Take top N
        top_memories = scored_memories[:limit]

        # Step 5: Update retrieval counts for selected memories
        for memory, _ in top_memories:
            try:
                await self.storage.update_retrieval_count(memory.id)
            except Exception as e:
                logger.warning(f"Failed to update retrieval count: {e}")

        logger.info(
            f"Retrieved {len(top_memories)} relevant memories (from {len(candidates)} candidates)"
        )

        return top_memories

    async def _calculate_importance_score(
        self, memory: MemoryRecord, query_context: dict[str, Any]
    ) -> ImportanceScore:
        """
        Calculate weighted importance score for a memory.

        Scoring factors:
        - Recency: Recent memories more valuable (exponential decay, half-life=7 days)
        - Success: Successful executions weighted higher (1.0 vs 0.3)
        - Relevance: Semantic similarity to current task (0.0-1.0)
        - Frequency: Retrieved memories gain importance (capped at 1.0)

        Weights:
        - Recency: 25%
        - Success: 30%
        - Relevance: 35%
        - Frequency: 10%

        Args:
            memory: Memory to score
            query_context: Current task context

        Returns:
            ImportanceScore with all components
        """
        # 1. Recency Score (exponential decay)
        days_old = (datetime.utcnow() - memory.timestamp).days
        recency_score = math.exp(-days_old / 7.0)  # Half-life = 7 days

        # 2. Success Score
        is_success = memory.content.get("success", False)
        success_score = 1.0 if is_success else 0.3

        # 3. Relevance Score (cosine similarity of embeddings)
        relevance_score = 0.5  # Default moderate relevance

        if "embedding" in query_context and memory.embedding:
            try:
                relevance_score = self._cosine_similarity(
                    query_context["embedding"], memory.embedding
                )
            except Exception as e:
                logger.debug(f"Failed to calculate relevance: {e}")

        # 4. Frequency Score (retrieval count, capped at 1.0)
        frequency_score = min(1.0, memory.retrieval_count / 10.0)

        # Calculate weighted total
        importance = ImportanceScore.calculate(
            recency=recency_score,
            success=success_score,
            relevance=relevance_score,
            frequency=frequency_score,
        )

        return importance

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """
        Calculate cosine similarity between two vectors.

        Args:
            vec1: First vector
            vec2: Second vector

        Returns:
            Similarity score (0.0 to 1.0)
        """
        if len(vec1) != len(vec2):
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = math.sqrt(sum(a * a for a in vec1))
        magnitude2 = math.sqrt(sum(b * b for b in vec2))

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        similarity = dot_product / (magnitude1 * magnitude2)

        # Normalize to 0-1 range (cosine similarity is -1 to 1)
        return (similarity + 1.0) / 2.0

    async def search_similar_tasks(
        self, query_embedding: list[float], limit: int = 10
    ) -> list[MemoryRecord]:
        """
        Search for similar past tasks using vector similarity.

        Args:
            query_embedding: Query vector
            limit: Maximum results

        Returns:
            List of similar task memories
        """
        return await self.storage.search_similar(
            embedding=query_embedding,
            limit=limit,
            memory_type=MemoryType.TASK_EXECUTION,
        )

    async def get_recent_failures(
        self, since: datetime | None = None, limit: int = 10
    ) -> list[MemoryRecord]:
        """
        Get recent failure patterns for proactive recovery.

        Args:
            since: Only failures after this time (default: last 24h)
            limit: Maximum failures to return

        Returns:
            List of failure pattern memories
        """
        if since is None:
            since = datetime.utcnow() - timedelta(hours=24)

        # Get all failure patterns, then filter by time
        failures = await self.storage.search_memories(
            memory_type=MemoryType.FAILURE_PATTERN, limit=limit * 2
        )

        # Filter by timestamp
        recent = [f for f in failures if f.timestamp >= since]

        return recent[:limit]

    async def get_success_strategies(
        self, task_type: str | None = None, limit: int = 5
    ) -> list[MemoryRecord]:
        """
        Get proven successful strategies.

        Args:
            task_type: Filter by applicable task type (optional)
            limit: Maximum strategies to return

        Returns:
            List of success strategy memories
        """
        strategies = await self.storage.search_memories(
            memory_type=MemoryType.SUCCESS_STRATEGY, limit=limit
        )

        if task_type:
            task_type_lower = task_type.lower()
            filtered: list[MemoryRecord] = []
            for strategy in strategies:
                tags = strategy.metadata.get("task_types", [])
                if isinstance(tags, list):
                    tags_lower = {str(tag).lower() for tag in tags}
                    if task_type_lower in tags_lower:
                        filtered.append(strategy)
                        continue
                content_task = str(strategy.content.get("task_type", "")).lower()
                if content_task == task_type_lower:
                    filtered.append(strategy)
            return filtered[:limit]

        return strategies

    async def get_memory_stats(self) -> dict[str, Any]:
        """
        Get memory system statistics.

        Returns:
            Dictionary with counts and metrics
        """
        # Get counts by memory type
        stats = {}

        for mem_type in MemoryType:
            memories = await self.storage.search_memories(
                memory_type=mem_type, limit=1000
            )
            stats[mem_type.value] = len(memories)

        return stats

    # ========================================================================
    # Maintenance
    # ========================================================================

    async def cleanup_old_memories(self, older_than_days: int = 90) -> int:
        """
        Clean up old, low-importance memories.

        Args:
            older_than_days: Delete memories older than this

        Returns:
            Number of memories deleted
        """
        cutoff = datetime.utcnow() - timedelta(days=older_than_days)
        candidates = await self.storage.search_memories(limit=5000)
        deleted = 0

        for memory in candidates:
            if memory.timestamp >= cutoff:
                continue
            if memory.memory_type == MemoryType.FAILURE_PATTERN:
                continue
            if memory.importance_score >= 0.7:
                continue
            if await self.storage.delete_memory(memory.id):
                deleted += 1

        logger.info(
            "Cleanup complete: deleted %s old low-importance memories older than %s days",
            deleted,
            older_than_days,
        )
        return deleted
