from __future__ import annotations

import asyncio
import base64
import gzip
import logging
import queue
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any

import paramiko

from logging_targets import ps_quote


class AsyncBatchHandler(logging.Handler):
    """Non-blocking logging handler that flushes records in a worker thread."""

    def __init__(
        self,
        *,
        flush_interval_seconds: float,
        batch_size: int,
        queue_size: int = 20000,
    ) -> None:
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
        try:
            line = self.format(record)
            self._queue.put_nowait(line)
        except queue.Full:
            self._dropped += 1
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        self._stop.set()
        self._worker.join(timeout=3.0)
        super().close()

    def _run(self) -> None:
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
        try:
            self._flush_batch(batch)
        except Exception as exc:
            if "ssh mirror backoff active" in str(exc):
                self._stderr(f"Log sink flush deferred: {exc}")
                return
            self._stderr("Log sink flush failed:\n" + traceback.format_exc())

    def _flush_batch(self, batch: list[str]) -> None:
        raise NotImplementedError

    @staticmethod
    def _stderr(message: str) -> None:
        sys.stderr.write(message.rstrip() + "\n")
        sys.stderr.flush()


class SSHMirrorFileHandler(AsyncBatchHandler):
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
        super().__init__(flush_interval_seconds=flush_interval_seconds, batch_size=batch_size)
        self._host = host
        self._port = port
        self._username = username
        self._key_path = key_path
        self._password = password
        self._strict_host_key = strict_host_key
        self._connect_timeout = max(8, int(connect_timeout))
        self._command_timeout = max(10, int(command_timeout))
        self._remote_windows_path = remote_windows_path
        self._client: paramiko.SSHClient | None = None
        self._mkdir_done = False
        self._connect_failures = 0
        self._next_connect_after_monotonic = 0.0

    def close(self) -> None:
        super().close()
        self._close_client()

    def _close_client(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
            self._mkdir_done = False

    def _connect(self) -> paramiko.SSHClient:
        if self._client is not None:
            return self._client
        now = time.monotonic()
        if now < self._next_connect_after_monotonic:
            raise RuntimeError(
                f"ssh mirror backoff active ({self._next_connect_after_monotonic - now:.1f}s remaining)"
            )
        client = paramiko.SSHClient()
        if self._strict_host_key:
            client.load_system_host_keys()
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        explicit_auth = bool(self._key_path or self._password)
        kwargs: dict[str, object] = {
            "hostname": self._host,
            "port": self._port,
            "username": self._username,
            "timeout": self._connect_timeout,
            "auth_timeout": self._connect_timeout,
            "banner_timeout": self._connect_timeout,
            "look_for_keys": not explicit_auth,
            "allow_agent": not explicit_auth,
        }
        if self._key_path:
            kwargs["key_filename"] = self._key_path
        if self._password:
            kwargs["password"] = self._password

        try:
            client.connect(**kwargs)
        except Exception:
            self._connect_failures = min(self._connect_failures + 1, 6)
            self._next_connect_after_monotonic = time.monotonic() + (2 ** self._connect_failures)
            raise
        self._connect_failures = 0
        self._next_connect_after_monotonic = 0.0
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(15)
        self._client = client
        return client

    def _flush_batch(self, batch: list[str]) -> None:
        if not self._mkdir_done:
            client = self._connect()
            parent = self._remote_windows_path.replace("/", "\\")
            if "\\" in parent:
                parent = parent.rsplit("\\", 1)[0]
            mkdir_script = (
                "$ErrorActionPreference='Stop';"
                f"$d={ps_quote(parent)};"
                "if ($d -and -not (Test-Path -LiteralPath $d)) { "
                "New-Item -ItemType Directory -Path $d -Force | Out-Null }"
            )
            self._exec_powershell(client, mkdir_script, timeout=self._command_timeout)
            self._mkdir_done = True

        max_mirror_line_chars = 500
        for i, line in enumerate(batch):
            if len(line) > max_mirror_line_chars:
                batch[i] = line[:max_mirror_line_chars] + "...[truncated]"

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
                        self._stderr("SSH mirror transient channel close; skipped one log chunk.")
                        return
                    raise

    def _append_chunk(self, client: paramiko.SSHClient, lines: list[str]) -> None:
        payload = "".join(f"{line}\n" for line in lines)
        encoded_payload = base64.b64encode(payload.encode("utf-8")).decode("ascii")
        script = (
            "$ErrorActionPreference='Stop';"
            f"$p={ps_quote(self._remote_windows_path)};"
            f"$b=[System.Convert]::FromBase64String('{encoded_payload}');"
            "$t=[System.Text.Encoding]::UTF8.GetString($b);"
            "Add-Content -LiteralPath $p -Value $t -Encoding UTF8"
        )
        self._exec_powershell(client, script, timeout=self._command_timeout)

    def _exec_powershell(
        self,
        client: paramiko.SSHClient,
        script: str,
        timeout: int | None = None,
    ) -> None:
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


class WebSocketBatchHandler(AsyncBatchHandler):
    """Mirror logs to a connected WebSocket agent for local file write."""

    def __init__(
        self,
        *,
        stream: str,
        flush_interval_seconds: float = 1.0,
        batch_size: int = 20,
    ) -> None:
        self._stream = stream
        self._loop: asyncio.AbstractEventLoop | None = None
        self._warned_missing_loop = False
        super().__init__(flush_interval_seconds=flush_interval_seconds, batch_size=batch_size)

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _flush_batch(self, batch: list[str]) -> None:
        loop = self._loop
        if loop is None:
            try:
                import gateway

                gateway.set_websocket_log_mirror_state(
                    enabled=True,
                    loop_bound=False,
                    last_error="event_loop_unbound",
                )
            except Exception:
                pass
            if not self._warned_missing_loop:
                self._warned_missing_loop = True
                self._stderr(
                    f"WebSocket log mirror disabled for stream '{self._stream}' because no event loop is bound."
                )
            return
        try:
            import gateway

            future = asyncio.run_coroutine_threadsafe(
                gateway.send_log_write(self._stream, list(batch)),
                loop,
            )
            future.result(timeout=5.0)
            gateway.set_websocket_log_mirror_state(
                enabled=True,
                loop_bound=True,
                last_error="",
            )
        except Exception:
            try:
                gateway.set_websocket_log_mirror_state(
                    enabled=True,
                    loop_bound=loop is not None,
                    last_error=traceback.format_exc()[:1000],
                )
            except Exception:
                pass


class S3BatchHandler(AsyncBatchHandler):
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
        if self._client is None:
            import boto3

            self._client = boto3.client("s3", region_name=self._region)
        return self._client

    def _flush_batch(self, batch: list[str]) -> None:
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
                self._stderr("S3 log sink disabled because AWS credentials are not configured.")
                return
            raise


def safe_close_handler(handler: logging.Handler) -> None:
    try:
        handler.close()
    except Exception:
        pass
