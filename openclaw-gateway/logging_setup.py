from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable


_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def configure_logging(
    *,
    level_name: str,
    log_dir: str,
    mirror_log_dir: str | None = None,
    max_bytes: int = 25 * 1024 * 1024,
    backup_count: int = 10,
) -> None:
    """
    Configure root + flow logging with console and rotating file handlers.

    Runtime logs are written to:
      - <log_dir>/skynet-runtime.log
      - <mirror_log_dir>/skynet-runtime.log (if available)

    Detailed control-flow logs are written to:
      - <log_dir>/skynet-control-flow.log
      - <mirror_log_dir>/skynet-control-flow.log (if available)
    """
    level = getattr(logging, str(level_name).upper(), logging.INFO)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(_DEFAULT_FORMAT, datefmt=_DEFAULT_DATEFMT)
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    runtime_targets = _resolve_targets(log_dir, mirror_log_dir, "skynet-runtime.log")
    for target in runtime_targets:
        handler = RotatingFileHandler(
            target,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(fmt)
        root.addHandler(handler)

    flow_logger = logging.getLogger("skynet.flow")
    flow_logger.handlers.clear()
    flow_logger.setLevel(logging.DEBUG)
    flow_logger.propagate = True

    flow_targets = _resolve_targets(log_dir, mirror_log_dir, "skynet-control-flow.log")
    for target in flow_targets:
        flow_handler = RotatingFileHandler(
            target,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        flow_handler.setLevel(logging.DEBUG)
        flow_handler.setFormatter(fmt)
        flow_logger.addHandler(flow_handler)

    root.info(
        "Logging configured level=%s runtime_targets=%s flow_targets=%s",
        logging.getLevelName(level),
        [str(p) for p in runtime_targets],
        [str(p) for p in flow_targets],
    )


def _resolve_targets(
    log_dir: str,
    mirror_log_dir: str | None,
    filename: str,
) -> list[Path]:
    targets: list[Path] = []
    for directory in _iter_dirs(log_dir, mirror_log_dir):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            targets.append(directory / filename)
        except Exception:
            logging.getLogger("skynet").exception(
                "Failed preparing log directory: %s",
                directory,
            )
    return targets


def _iter_dirs(primary: str, mirror: str | None) -> Iterable[Path]:
    seen: set[str] = set()
    for raw in (primary, mirror):
        value = (raw or "").strip()
        if not value:
            continue
        normalized = str(Path(value))
        if normalized in seen:
            continue
        seen.add(normalized)
        yield Path(value)
