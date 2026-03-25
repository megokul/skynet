from __future__ import annotations

import atexit
import logging
from logging.handlers import RotatingFileHandler
from typing import Any

from logging_handlers import (
    S3BatchHandler as _S3BatchHandler,
    SSHMirrorFileHandler as _SSHMirrorFileHandler,
    WebSocketBatchHandler as _WebSocketBatchHandler,
    safe_close_handler as _safe_close_handler,
)
from logging_targets import (
    join_windows_path as _join_windows_path,
    resolve_targets as _resolve_targets,
)


_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def configure_logging(
    *,
    level_name: str,
    log_dir: str,
    mirror_log_dir: str | None = None,
    max_bytes: int = 25 * 1024 * 1024,
    backup_count: int = 10,
    enable_local_file_targets: bool = True,
    enable_ssh_mirror: bool = False,
    ssh_host: str = "",
    ssh_port: int = 22,
    ssh_user: str = "",
    ssh_key_path: str = "",
    ssh_password: str = "",
    ssh_strict_host_key: bool = False,
    ssh_connect_timeout: int = 4,
    ssh_command_timeout: int = 180,
    enable_s3_logs: bool = False,
    s3_bucket: str = "",
    s3_prefix: str = "openclaw/logs",
    s3_region: str = "us-east-1",
    enable_websocket_mirror: bool = False,
) -> dict[str, Any]:
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    fmt = logging.Formatter(_DEFAULT_FORMAT, datefmt=_DEFAULT_DATEFMT)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    flow_logger = logging.getLogger("skynet.flow")
    for handler in list(flow_logger.handlers):
        flow_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    flow_logger.setLevel(logging.DEBUG)
    flow_logger.propagate = True

    trace_mirror_logger = logging.getLogger("skynet.trace.mirror")
    for handler in list(trace_mirror_logger.handlers):
        trace_mirror_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    trace_mirror_logger.setLevel(logging.DEBUG)
    trace_mirror_logger.propagate = False
    trace_fmt = logging.Formatter("%(message)s")

    runtime_targets: list[Any] = []
    flow_targets: list[Any] = []
    if enable_local_file_targets:
        runtime_targets = _resolve_targets(log_dir, None, "skynet-runtime.log")
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

        flow_targets = _resolve_targets(log_dir, None, "skynet-control-flow.log")
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

    ssh_runtime_target = ""
    ssh_flow_target = ""
    ssh_trace_target = ""
    if enable_ssh_mirror and mirror_log_dir and ssh_host and ssh_user:
        ssh_runtime_target = _join_windows_path(mirror_log_dir, "skynet-runtime.log")
        ssh_flow_target = _join_windows_path(mirror_log_dir, "skynet-control-flow.log")
        ssh_trace_target = _join_windows_path(mirror_log_dir, "skynet.trace.log")

        ssh_runtime = _SSHMirrorFileHandler(
            host=ssh_host,
            port=int(ssh_port),
            username=ssh_user,
            key_path=ssh_key_path,
            password=ssh_password,
            strict_host_key=ssh_strict_host_key,
            connect_timeout=ssh_connect_timeout,
            command_timeout=ssh_command_timeout,
            remote_windows_path=ssh_runtime_target,
        )
        ssh_runtime.setLevel(logging.DEBUG)
        ssh_runtime.setFormatter(fmt)
        root.addHandler(ssh_runtime)
        atexit.register(_safe_close_handler, ssh_runtime)

        ssh_flow = _SSHMirrorFileHandler(
            host=ssh_host,
            port=int(ssh_port),
            username=ssh_user,
            key_path=ssh_key_path,
            password=ssh_password,
            strict_host_key=ssh_strict_host_key,
            connect_timeout=ssh_connect_timeout,
            command_timeout=ssh_command_timeout,
            remote_windows_path=ssh_flow_target,
        )
        ssh_flow.setLevel(logging.DEBUG)
        ssh_flow.setFormatter(fmt)
        flow_logger.addHandler(ssh_flow)
        atexit.register(_safe_close_handler, ssh_flow)

        ssh_trace = _SSHMirrorFileHandler(
            host=ssh_host,
            port=int(ssh_port),
            username=ssh_user,
            key_path=ssh_key_path,
            password=ssh_password,
            strict_host_key=ssh_strict_host_key,
            connect_timeout=ssh_connect_timeout,
            command_timeout=ssh_command_timeout,
            remote_windows_path=ssh_trace_target,
        )
        ssh_trace.setLevel(logging.DEBUG)
        ssh_trace.setFormatter(trace_fmt)
        trace_mirror_logger.addHandler(ssh_trace)
        atexit.register(_safe_close_handler, ssh_trace)

    s3_runtime_target = ""
    s3_flow_target = ""
    s3_trace_target = ""
    if enable_s3_logs and s3_bucket:
        s3_runtime_target = f"s3://{s3_bucket}/{s3_prefix.strip('/')}/runtime-logs/runtime/"
        s3_flow_target = f"s3://{s3_bucket}/{s3_prefix.strip('/')}/runtime-logs/control-flow/"
        s3_trace_target = f"s3://{s3_bucket}/{s3_prefix.strip('/')}/runtime-logs/trace/"

        s3_runtime = _S3BatchHandler(
            bucket=s3_bucket,
            prefix=s3_prefix,
            region=s3_region,
            stream="runtime",
            flush_interval_seconds=5.0,
            batch_size=300,
        )
        s3_runtime.setLevel(logging.DEBUG)
        s3_runtime.setFormatter(fmt)
        root.addHandler(s3_runtime)
        atexit.register(_safe_close_handler, s3_runtime)

        s3_flow = _S3BatchHandler(
            bucket=s3_bucket,
            prefix=s3_prefix,
            region=s3_region,
            stream="control-flow",
            flush_interval_seconds=5.0,
            batch_size=150,
        )
        s3_flow.setLevel(logging.DEBUG)
        s3_flow.setFormatter(fmt)
        flow_logger.addHandler(s3_flow)
        atexit.register(_safe_close_handler, s3_flow)

        s3_trace = _S3BatchHandler(
            bucket=s3_bucket,
            prefix=s3_prefix,
            region=s3_region,
            stream="trace",
            flush_interval_seconds=5.0,
            batch_size=80,
        )
        s3_trace.setLevel(logging.DEBUG)
        s3_trace.setFormatter(trace_fmt)
        trace_mirror_logger.addHandler(s3_trace)
        atexit.register(_safe_close_handler, s3_trace)

    ws_runtime_target = ""
    ws_flow_target = ""
    ws_trace_target = ""
    websocket_handlers: list[_WebSocketBatchHandler] = []
    if enable_websocket_mirror:
        ws_runtime_target = "ws://agent/runtime"
        ws_flow_target = "ws://agent/control-flow"
        ws_trace_target = "ws://agent/trace"

        ws_runtime = _WebSocketBatchHandler(stream="runtime")
        ws_runtime.setLevel(logging.DEBUG)
        ws_runtime.setFormatter(fmt)
        root.addHandler(ws_runtime)
        atexit.register(_safe_close_handler, ws_runtime)
        websocket_handlers.append(ws_runtime)

        ws_flow = _WebSocketBatchHandler(stream="control-flow")
        ws_flow.setLevel(logging.DEBUG)
        ws_flow.setFormatter(fmt)
        flow_logger.addHandler(ws_flow)
        atexit.register(_safe_close_handler, ws_flow)
        websocket_handlers.append(ws_flow)

        ws_trace = _WebSocketBatchHandler(stream="trace")
        ws_trace.setLevel(logging.DEBUG)
        ws_trace.setFormatter(trace_fmt)
        trace_mirror_logger.addHandler(ws_trace)
        atexit.register(_safe_close_handler, ws_trace)
        websocket_handlers.append(ws_trace)

    root.info(
        "Logging configured level=%s local_file_targets=%s ssh_runtime_target=%s ssh_flow_target=%s ssh_trace_target=%s s3_runtime_target=%s s3_flow_target=%s s3_trace_target=%s ws_runtime=%s ws_flow=%s ws_trace=%s",
        logging.getLevelName(level),
        [str(p) for p in runtime_targets + flow_targets],
        ssh_runtime_target,
        ssh_flow_target,
        ssh_trace_target,
        s3_runtime_target,
        s3_flow_target,
        s3_trace_target,
        ws_runtime_target,
        ws_flow_target,
        ws_trace_target,
    )
    return {
        "websocket_handlers": websocket_handlers,
        "websocket_enabled": bool(enable_websocket_mirror),
    }
