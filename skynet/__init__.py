"""
SKYNET — AI-Powered Development Platform

A distributed AI software development platform with:
- Orchestration via SKYNET Core
- Execution via CHATHAN Protocol
- Multiple provider support (OpenClaw, Docker, SSH)

Main Components:
- skynet.gateway: Telegram bot interface
- skynet.core: Orchestration and planning
- skynet.policy: Security and approval rules
- skynet.ledger: Job and worker state management
- skynet.queue: Async job queue (Celery + Redis)
- skynet.chathan: Execution protocol and providers
- skynet.sentinel: Monitoring and alerts
- skynet.archive: Memory and artifact storage
- skynet.shared: Common utilities

Usage:
    from skynet.shared.settings import *
    from skynet.shared.logging import get_logger
    
    logger = get_logger(__name__)
    logger.info("SKYNET starting...")
"""

# Version
__version__ = "1.0.0"
__codename__ = "CHATHAN"

# Main exports
from skynet.shared.settings import (
    PROJECT_NAME,
    CODENAME,
    VERSION,
    AUTH_TOKEN,
    TELEGRAM_BOT_TOKEN,
)

from skynet.shared.logging import get_logger

from skynet.ledger.models import (
    Job,
    Worker,
    JobLock,
    JobStatus,
    WorkerStatus,
    RiskLevel,
    PlanSpec,
)

__all__ = [
    # Version
    "__version__",
    "__codename__",
    # Settings
    "PROJECT_NAME",
    "CODENAME",
    "VERSION",
    "AUTH_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    # Logging
    "get_logger",
    # Models
    "Job",
    "Worker",
    "JobLock",
    "JobStatus",
    "WorkerStatus",
    "RiskLevel",
    "PlanSpec",
]
