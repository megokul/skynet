"""
SKYNET — Shared Logging Configuration

Centralized logging setup for all SKYNET components.
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from .settings import LOG_LEVEL, LOG_DIR


# =============================================================================
# Log Format
# =============================================================================
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


# =============================================================================
# Logger Factory
# =============================================================================
def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """
    Get a configured logger for a component.
    
    Args:
        name: Logger name (typically __name__ of the module)
        level: Optional log level override
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Avoid adding handlers multiple times
    if logger.handlers:
        return logger
    
    # Set level
    log_level = getattr(logging, (level or LOG_LEVEL).upper(), logging.INFO)
    logger.setLevel(log_level)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(
        logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    )
    logger.addHandler(console_handler)
    
    return logger


def configure_file_logging(
    logger: logging.Logger,
    filename: str,
    max_bytes: int = 10_000_000,  # 10MB
    backup_count: int = 5,
) -> None:
    """
    Add file logging to a logger with rotation.
    
    Args:
        logger: Logger to configure
        filename: Name of the log file (will be in LOG_DIR)
        max_bytes: Maximum file size before rotation
        backup_count: Number of backup files to keep
    """
    log_path = Path(LOG_DIR)
    log_path.mkdir(parents=True, exist_ok=True)
    
    from logging.handlers import RotatingFileHandler
    
    file_handler = RotatingFileHandler(
        log_path / filename,
        maxBytes=max_bytes,
        backupCount=backup_count,
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    logger.addHandler(file_handler)


# =============================================================================
# Component Loggers
# =============================================================================
# Pre-configured loggers for major components
GATEWAY_LOGGER = "skynet.gateway"
CORE_LOGGER = "skynet.core"
ORCHESTRATOR_LOGGER = "skynet.orchestrator"
POLICY_LOGGER = "skynet.policy"
LEDGER_LOGGER = "skynet.ledger"
QUEUE_LOGGER = "skynet.queue"
CHATHAN_LOGGER = "skynet.chathan"
PROVIDER_LOGGER = "skynet.provider"
SENTINEL_LOGGER = "skynet.sentinel"
ARCHIVE_LOGGER = "skynet.archive"
WORKER_LOGGER = "skynet.worker"


# =============================================================================
# Banner / Logo
# =============================================================================
def get_banner(ws_port: int = 8765, http_port: int = 8766) -> str:
    """Generate the SKYNET startup banner."""
    return f"""
  ____  _  ____   ___   _ _____ _____ 
 / ___|| |/ /\ \ / / \ | | ____|_   _|
 \___ \| ' /  \ V /|  \| |  _|   | | 
  ___) | . \   | | | |\  | |___  | | 
 |____/|_|\_\  |_| |_| \_|_____| |_| 
      Codename: CHATHAN

  WebSocket : 0.0.0.0:{ws_port}
  HTTP API  : 127.0.0.1:{http_port}
  Telegram  : enabled
"""
