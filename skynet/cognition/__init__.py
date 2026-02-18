"""
SKYNET Cognition — Autonomous initiative and proactive intelligence.

Enables SKYNET to:
- Monitor system state continuously
- Identify optimization opportunities
- Initiate maintenance tasks autonomously
- Recover from errors proactively
- Learn from patterns

Core Components:
- InitiativeEngine: Main autonomous loop
- SystemStateMonitor: Monitors system health and opportunities
- InitiativeStrategy: Rules for when to take initiative
- SafetyConstraints: Limits on autonomous actions

Usage:
    from skynet.cognition import InitiativeEngine

    engine = InitiativeEngine(
        planner=planner,
        orchestrator=orchestrator,
        memory_manager=memory,
        event_bus=event_bus
    )

    await engine.start()
"""

from .initiative_engine import InitiativeEngine
from .monitors import SystemStateMonitor, SystemState
from .strategies import (
    MaintenanceStrategy,
    RecoveryStrategy,
    OptimizationStrategy,
)
from .constraints import SafetyConstraints, INITIATIVE_CONSTRAINTS

__all__ = [
    "InitiativeEngine",
    "SystemStateMonitor",
    "SystemState",
    "MaintenanceStrategy",
    "RecoveryStrategy",
    "OptimizationStrategy",
    "SafetyConstraints",
    "INITIATIVE_CONSTRAINTS",
]
