"""
SKYNET Memory System — Persistent Cognitive Memory.

Enables SKYNET to remember and learn from past executions, failures, and successes.

Components:
- models: Memory data structures
- storage: PostgreSQL/SQLite persistence layer
- memory_manager: High-level memory API
- vector_index: Semantic search via pgvector
"""

from .models import MemoryRecord, MemoryType, TaskMemory, SystemStateSnapshot
from .memory_manager import MemoryManager

__all__ = [
    "MemoryRecord",
    "MemoryType",
    "TaskMemory",
    "SystemStateSnapshot",
    "MemoryManager",
]
