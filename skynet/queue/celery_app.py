"""
SKYNET Queue — Celery Application

Job queue implementation using Celery + Redis.
Handles async job dispatch, retries, and scheduling.
"""

from __future__ import annotations

import os
from celery import Celery
from celery.signals import worker_init, worker_shutdown

# =============================================================================
# Configuration
# =============================================================================
# Redis URL (use environment variable or default)
REDIS_URL: str = os.environ.get(
    "SKYNET_REDIS_URL",
    os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
)

# Celery configuration
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True

# Task routing
CELERY_TASK_ROUTES = {
    "skynet.queue.tasks.execute_job": {"queue": "execution"},
    "skynet.queue.tasks.cleanup_job": {"queue": "maintenance"},
    "skynet.queue.tasks.health_check": {"queue": "monitoring"},
}

# Retry configuration
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_REJECT_ON_WORKER_LOST = True


# =============================================================================
# Create Celery App
# =============================================================================
celery_app = Celery("skynet")

# Load config from module
celery_app.config_from_object("skynet.queue.celery_app")

# Auto-discover tasks
celery_app.autodiscover_tasks(["skynet.queue"])

# Backwards compatibility
app = celery_app


# =============================================================================
# Lifecycle Signals
# =============================================================================
@worker_init.connect
def on_worker_init(**kwargs):
    """Called when worker starts."""
    import logging
    logger = logging.getLogger("skynet.queue")
    logger.info(f"SKYNET Queue worker initializing, broker: {REDIS_URL}")


@worker_shutdown.connect
def on_worker_shutdown(**kwargs):
    """Called when worker stops."""
    import logging
    logger = logging.getLogger("skynet.queue")
    logger.info("SKYNET Queue worker shutting down")


# =============================================================================
# Helper Functions
# =============================================================================
def enqueue_job(
    job_id: str,
    execution_spec: dict,
    queue: str = "execution",
    countdown: int | None = None,
) -> str:
    """
    Enqueue a job for execution.

    Args:
        job_id: Unique job identifier
        execution_spec: ExecutionSpec as dict
        queue: Queue name to route to
        countdown: Optional delay in seconds before executing

    Returns:
        Celery task ID
    """
    from skynet.queue.worker import execute_job

    result = execute_job.apply_async(
        args=[job_id, execution_spec],
        queue=queue,
        countdown=countdown,
        task_id=job_id,  # Use job_id as task_id for easy tracking
    )

    return result.id


def cancel_job(job_id: str) -> bool:
    """
    Cancel a queued or running job.
    
    Args:
        job_id: Job ID to cancel
        
    Returns:
        True if job was cancelled, False otherwise
    """
    from celery import current_app
    
    result = current_app.control.revoke(job_id, terminate=True)
    return result is not None


def get_job_status(job_id: str) -> dict | None:
    """
    Get the status of a job.
    
    Args:
        job_id: Job ID to check
        
    Returns:
        Dict with status info or None if not found
    """
    from celery.result import AsyncResult
    
    try:
        result = AsyncResult(job_id)
        return {
            "job_id": job_id,
            "status": result.state,
            "ready": result.ready(),
            "successful": result.successful() if result.ready() else None,
            "result": result.result if result.ready() else None,
            "traceback": result.traceback if result.failed() else None,
        }
    except Exception:
        return None


def get_queue_stats() -> dict:
    """
    Get statistics about queued jobs.
    
    Returns:
        Dict with queue statistics
    """
    inspector = app.control.inspect()
    
    # Get active tasks
    active = inspector.active()
    
    # Get scheduled tasks
    scheduled = inspector.scheduled()
    
    # Get registered tasks
    registered = inspector.registered()
    
    return {
        "active": active or {},
        "scheduled": scheduled or {},
        "registered": registered or {},
    }
