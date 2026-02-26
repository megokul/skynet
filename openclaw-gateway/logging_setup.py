"""Gateway logging bootstrap and transport fan-out.

Purpose:
- Configure rotating local log files and structured formatting.
- Optionally mirror logs to remote Windows hosts over SSH.
- Keep log writes non-blocking so runtime latency stays stable.

How it works:
- Uses async queue-backed handlers for batch flushing in worker threads.
- Encodes payloads safely for remote command transport.
- Falls back to stderr diagnostics if mirror sinks fail.

Why this exists:
- Centralized setup keeps every process emitting consistent log shape.
- Non-blocking handlers prevent user-facing operations from stalling on IO.
- Mirror support allows debugging from remote operator workstations."""

from __future__ import annotations

import atexit
import base64
import gzip
import logging
import queue
import sys
import threading
import traceback
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

import paramiko


_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%dT%H:%M:%S"


class _AsyncBatchHandler(logging.Handler):
    """Non-blocking logging handler that flushes records in a worker thread."""

    def __init__(
        self,
        *,
        flush_interval_seconds: float,
        batch_size: int,
        queue_size: int = 20000,
    ) -> None:
        """
        Initialize runtime dependencies and object state.
        
        Purpose:
        - Implement `__init__` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `flush_interval_seconds`: input used by this function to compute or route work.
        - `batch_size`: input used by this function to compute or route work.
        - `queue_size`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        super().__init__()
        self._flush_interval_seconds = max(0.1, float(flush_interval_seconds))
        self._batch_size = max(1, int(batch_size))
        self._queue: queue.Queue[str] = queue.Queue(maxsize=max(100, int(queue_size)))
        self._stop = threading.Event()
        self._dropped = 0
        self._worker = threading.Thread(
            target=self._run,
            name=f"{self.__class__.__name__}-worker",
            daemon=True,
        )
        self._worker.start()

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit.
        
        Purpose:
        - Implement `emit` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `record`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        try:
            line = self.format(record)
            self._queue.put_nowait(line)
        except queue.Full:
            self._dropped += 1
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        """
        Close.
        
        Purpose:
        - Implement `close` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        self._stop.set()
        self._worker.join(timeout=3.0)
        super().close()

    def _run(self) -> None:
        """
        Run.
        
        Purpose:
        - Implement `_run` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        pending: list[str] = []
        while not self._stop.is_set() or not self._queue.empty():
            try:
                line = self._queue.get(timeout=self._flush_interval_seconds)
                pending.append(line)
                if len(pending) >= self._batch_size:
                    self._flush_safe(pending)
                    pending.clear()
            except queue.Empty:
                if pending:
                    self._flush_safe(pending)
                    pending.clear()
            except Exception:
                self._stderr("Unexpected async log worker failure:\n" + traceback.format_exc())
        if pending:
            self._flush_safe(pending)
        if self._dropped:
            self._stderr(
                f"{self.__class__.__name__} dropped {self._dropped} log lines due to queue pressure."
            )

    def _flush_safe(self, batch: list[str]) -> None:
        """
        Flush safe.
        
        Purpose:
        - Implement `_flush_safe` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `batch`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        try:
            self._flush_batch(batch)
        except Exception:
            self._stderr("Log sink flush failed:\n" + traceback.format_exc())

    def _flush_batch(self, batch: list[str]) -> None:
        """
        Flush batch.
        
        Purpose:
        - Implement `_flush_batch` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `batch`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        raise NotImplementedError

    @staticmethod
    def _stderr(message: str) -> None:
        """
        Stderr.
        
        Purpose:
        - Implement `_stderr` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `message`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        sys.stderr.write(message.rstrip() + "\n")
        sys.stderr.flush()


class _SSHMirrorFileHandler(_AsyncBatchHandler):
    """Write logs to a Windows path over SSH."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        key_path: str,
        password: str,
        strict_host_key: bool,
        connect_timeout: int,
        command_timeout: int,
        remote_windows_path: str,
        flush_interval_seconds: float = 1.0,
        batch_size: int = 20,
    ) -> None:
        """
        Initialize runtime dependencies and object state.
        
        Purpose:
        - Implement `__init__` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `host`: input used by this function to compute or route work.
        - `port`: input used by this function to compute or route work.
        - `username`: input used by this function to compute or route work.
        - `key_path`: input used by this function to compute or route work.
        - `password`: input used by this function to compute or route work.
        - `strict_host_key`: input used by this function to compute or route work.
        - `connect_timeout`: input used by this function to compute or route work.
        - `command_timeout`: input used by this function to compute or route work.
        - `remote_windows_path`: input used by this function to compute or route work.
        - `flush_interval_seconds`: input used by this function to compute or route work.
        - `batch_size`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        super().__init__(flush_interval_seconds=flush_interval_seconds, batch_size=batch_size)
        self._host = host
        self._port = port
        self._username = username
        self._key_path = key_path
        self._password = password
        self._strict_host_key = strict_host_key
        self._connect_timeout = max(2, int(connect_timeout))
        self._command_timeout = max(10, int(command_timeout))
        self._remote_windows_path = remote_windows_path
        self._client: paramiko.SSHClient | None = None
        self._mkdir_done = False

    def close(self) -> None:
        """
        Close.
        
        Purpose:
        - Implement `close` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        super().close()
        self._close_client()

    def _close_client(self) -> None:
        """
        Close client.
        
        Purpose:
        - Implement `_close_client` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
            self._mkdir_done = False

    def _connect(self) -> paramiko.SSHClient:
        """
        Connect.
        
        Purpose:
        - Implement `_connect` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `paramiko.SSHClient` when available; otherwise side effects only.
        """

        if self._client is not None:
            return self._client
        client = paramiko.SSHClient()
        if self._strict_host_key:
            client.load_system_host_keys()
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs: dict[str, object] = {
            "hostname": self._host,
            "port": self._port,
            "username": self._username,
            "timeout": self._connect_timeout,
            "auth_timeout": self._connect_timeout,
            "banner_timeout": self._connect_timeout,
            "look_for_keys": True,
            "allow_agent": True,
        }
        if self._key_path:
            kwargs["key_filename"] = self._key_path
        if self._password:
            kwargs["password"] = self._password

        client.connect(**kwargs)
        self._client = client
        return client

    def _flush_batch(self, batch: list[str]) -> None:
        """
        Flush batch.
        
        Purpose:
        - Implement `_flush_batch` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `batch`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        if not self._mkdir_done:
            client = self._connect()
            parent = self._remote_windows_path.replace("/", "\\")
            if "\\" in parent:
                parent = parent.rsplit("\\", 1)[0]
            mkdir_script = (
                "$ErrorActionPreference='Stop';"
            f"$d={_ps_quote(parent)};"
            "if ($d -and -not (Test-Path -LiteralPath $d)) { "
            "New-Item -ItemType Directory -Path $d -Force | Out-Null }"
        )
            self._exec_powershell(client, mkdir_script, timeout=self._command_timeout)
            self._mkdir_done = True

        # Keep payload chunks small to avoid long command-line limits.
        chunk: list[str] = []
        size = 0
        for line in batch:
            line_len = len(line) + 1
            if chunk and size + line_len > 700:
                self._append_chunk_with_retry(chunk)
                chunk = []
                size = 0
            chunk.append(line)
            size += line_len
        if chunk:
            self._append_chunk_with_retry(chunk)

    def _append_chunk_with_retry(self, lines: list[str]) -> None:
        """
        Append chunk with retry.
        
        Purpose:
        - Implement `_append_chunk_with_retry` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `lines`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        for attempt in range(2):
            client = self._connect()
            try:
                self._append_chunk(client, lines)
                return
            except Exception:
                exc = sys.exc_info()[1]
                self._close_client()
                if attempt == 1:
                    if exc and "Channel closed" in str(exc):
                        self._stderr(
                            "SSH mirror transient channel close; skipped one log chunk."
                        )
                        return
                    raise

    def _append_chunk(self, client: paramiko.SSHClient, lines: list[str]) -> None:
        """
        Append chunk.
        
        Purpose:
        - Implement `_append_chunk` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `client`: input used by this function to compute or route work.
        - `lines`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        payload = "".join(f"{line}\n" for line in lines)
        encoded_payload = base64.b64encode(payload.encode("utf-8")).decode("ascii")
        script = (
            "$ErrorActionPreference='Stop';"
            f"$p={_ps_quote(self._remote_windows_path)};"
            f"$b=[System.Convert]::FromBase64String('{encoded_payload}');"
            "$t=[System.Text.Encoding]::UTF8.GetString($b);"
            "Add-Content -LiteralPath $p -Value $t -Encoding UTF8"
        )
        self._exec_powershell(
            client,
            script,
            timeout=self._command_timeout,
        )

    def _exec_powershell(
        self,
        client: paramiko.SSHClient,
        script: str,
        timeout: int | None = None,
    ) -> None:
        """
        Exec powershell.
        
        Purpose:
        - Implement `_exec_powershell` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `client`: input used by this function to compute or route work.
        - `script`: input used by this function to compute or route work.
        - `timeout`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        command = (
            "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass "
            f"-EncodedCommand {encoded}"
        )
        try:
            stdin, stdout, stderr = client.exec_command(
                command,
                timeout=timeout or self._command_timeout,
            )
            try:
                stdin.close()
            except Exception:
                pass
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            rc = stdout.channel.recv_exit_status()
            if rc != 0:
                raise RuntimeError(f"rc={rc} stdout={out[:300]} stderr={err[:300]}")
        except Exception:
            self._close_client()
            raise


class _S3BatchHandler(_AsyncBatchHandler):
    """Upload logs to S3 as compressed batch objects."""

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str,
        region: str,
        stream: str,
        flush_interval_seconds: float = 5.0,
        batch_size: int = 300,
    ) -> None:
        """
        Initialize runtime dependencies and object state.
        
        Purpose:
        - Implement `__init__` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `bucket`: input used by this function to compute or route work.
        - `prefix`: input used by this function to compute or route work.
        - `region`: input used by this function to compute or route work.
        - `stream`: input used by this function to compute or route work.
        - `flush_interval_seconds`: input used by this function to compute or route work.
        - `batch_size`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        super().__init__(flush_interval_seconds=flush_interval_seconds, batch_size=batch_size)
        self._bucket = bucket
        self._prefix = prefix.strip().strip("/")
        self._region = region
        self._stream = stream
        self._client = None
        self._sequence = 0
        self._disabled = False
        self._disable_reason = ""

    def _get_client(self):
        """
        Get client.
        
        Purpose:
        - Implement `_get_client` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        if self._client is None:
            import boto3

            self._client = boto3.client("s3", region_name=self._region)
        return self._client

    def _flush_batch(self, batch: list[str]) -> None:
        """
        Flush batch.
        
        Purpose:
        - Implement `_flush_batch` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `batch`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        if self._disabled:
            return
        now = datetime.now(timezone.utc)
        self._sequence += 1
        payload = "".join(f"{line}\n" for line in batch).encode("utf-8")
        compressed = gzip.compress(payload)
        key = (
            f"{self._prefix}/runtime-logs/{self._stream}/"
            f"{now:%Y/%m/%d/%H}/"
            f"{now:%Y%m%dT%H%M%S}_{self._sequence:06d}.log.gz"
        )
        try:
            client = self._get_client()
            client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=compressed,
                ContentType="text/plain",
                ContentEncoding="gzip",
            )
        except Exception as exc:
            msg = str(exc)
            if "Unable to locate credentials" in msg:
                self._disabled = True
                self._disable_reason = msg
                self._stderr(
                    "S3 log sink disabled because AWS credentials are not configured."
                )
                return
            raise


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
) -> None:
    """
    Configure root + flow logging.

    Sinks can include:
      - Console
      - Local rotating files (optional)
      - Remote Windows mirror over SSH (optional)
      - S3 batch objects (optional)
    """
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

    # Human-readable trace mirror sink. The trace source writes raw blocks;
    # this logger must preserve exact content (no timestamp/prefix formatting).
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

    runtime_targets: list[Path] = []
    flow_targets: list[Path] = []
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

    root.info(
        "Logging configured level=%s local_file_targets=%s ssh_runtime_target=%s ssh_flow_target=%s ssh_trace_target=%s s3_runtime_target=%s s3_flow_target=%s s3_trace_target=%s",
        logging.getLevelName(level),
        [str(p) for p in runtime_targets + flow_targets],
        ssh_runtime_target,
        ssh_flow_target,
        ssh_trace_target,
        s3_runtime_target,
        s3_flow_target,
        s3_trace_target,
    )


def _safe_close_handler(handler: logging.Handler) -> None:
    """
    Safe close handler.
    
    Purpose:
    - Implement `_safe_close_handler` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `handler`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    try:
        handler.close()
    except Exception:
        pass


def _resolve_targets(
    log_dir: str,
    mirror_log_dir: str | None,
    filename: str,
) -> list[Path]:
    """
    Resolve targets.
    
    Purpose:
    - Implement `_resolve_targets` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `log_dir`: input used by this function to compute or route work.
    - `mirror_log_dir`: input used by this function to compute or route work.
    - `filename`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[Path]` when available; otherwise side effects only.
    """

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
    """
    Iter dirs.
    
    Purpose:
    - Implement `_iter_dirs` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `primary`: input used by this function to compute or route work.
    - `mirror`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `Iterable[Path]` when available; otherwise side effects only.
    """

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


def _join_windows_path(base: str, filename: str) -> str:
    """
    Join windows path.
    
    Purpose:
    - Implement `_join_windows_path` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `base`: input used by this function to compute or route work.
    - `filename`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    clean_base = (base or "").strip().rstrip("\\/")
    if not clean_base:
        return filename
    return f"{clean_base}\\{filename}"


def _ps_quote(value: str) -> str:
    """
    Ps quote.
    
    Purpose:
    - Implement `_ps_quote` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `value`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    return "'" + value.replace("'", "''") + "'"
