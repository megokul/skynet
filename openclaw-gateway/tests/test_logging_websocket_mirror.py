from __future__ import annotations

import asyncio
import logging
import threading

import pytest

import gateway
from logging_setup import _WebSocketBatchHandler, configure_logging


@pytest.mark.asyncio
async def test_websocket_batch_handler_uses_bound_loop(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls: list[tuple[str, list[str]]] = []

    async def _fake_send_log_write(stream: str, lines: list[str]) -> None:
        calls.append((stream, list(lines)))

    monkeypatch.setattr(gateway, "send_log_write", _fake_send_log_write)
    monkeypatch.setattr(gateway, "set_websocket_log_mirror_state", lambda **kwargs: None)

    state = configure_logging(
        level_name="INFO",
        log_dir=str(tmp_path),
        enable_local_file_targets=False,
        enable_websocket_mirror=True,
    )
    handler = state["websocket_handlers"][0]
    handler.set_event_loop(asyncio.get_running_loop())
    thread = threading.Thread(target=handler._flush_batch, args=(["line-1", "line-2"],))
    thread.start()
    thread.join(timeout=5)
    await asyncio.sleep(0.1)
    handler.close()

    assert ("runtime", ["line-1", "line-2"]) in calls


def test_websocket_batch_handler_marks_unbound_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    updates: list[dict[str, object]] = []
    monkeypatch.setattr(gateway, "set_websocket_log_mirror_state", lambda **kwargs: updates.append(kwargs))
    handler = _WebSocketBatchHandler(stream="trace")
    try:
        handler._flush_batch(["line-1"])
    finally:
        handler.close()
    assert updates
    assert updates[-1]["loop_bound"] is False
