from __future__ import annotations

import asyncio

import pytest

import telegram_poller


class _StubControlPlaneClient:
    def __init__(self, *, acquire_payload: dict | None = None, renew_payload: dict | None = None) -> None:
        self.acquire_payload = acquire_payload or {}
        self.renew_payload = renew_payload or {}
        self.release_calls: list[tuple[str, str]] = []

    async def acquire_lease(self, lease_name: str, *, owner_id: str, ttl_seconds: int) -> dict:
        payload = dict(self.acquire_payload)
        payload.setdefault("lease_name", lease_name)
        payload.setdefault("owner_id", owner_id)
        payload.setdefault("held", bool(payload.get("acquired", False)))
        payload.setdefault("expires_at", "2026-03-10T09:00:00+00:00")
        return payload

    async def renew_lease(self, lease_name: str, *, owner_id: str, ttl_seconds: int) -> dict:
        payload = dict(self.renew_payload)
        payload.setdefault("lease_name", lease_name)
        payload.setdefault("owner_id", owner_id)
        payload.setdefault("held", bool(payload.get("renewed", False)))
        payload.setdefault("expires_at", "2026-03-10T09:00:30+00:00")
        return payload

    async def release_lease(self, lease_name: str, *, owner_id: str) -> dict:
        self.release_calls.append((lease_name, owner_id))
        return {"ok": True}


@pytest.mark.asyncio
async def test_telegram_poller_lease_blocks_on_foreign_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telegram_poller.cfg, "TELEGRAM_POLLER_LEASE_ENABLED", True)
    client = _StubControlPlaneClient(
        acquire_payload={
            "acquired": False,
            "owner_id": "gw-remote",
            "held": True,
        }
    )

    controller = telegram_poller.TelegramPollerLeaseController(
        gateway_id="gw-local",
        bot_token="123:test-token",
        client=client,
    )
    acquired = await controller.acquire()

    assert acquired is False
    status = telegram_poller.get_telegram_poller_status()
    assert status["telegram_poller_state"] == "blocked"
    assert status["telegram_poller_lease_owner"] == "gw-remote"
    assert status["telegram_poller_lock_healthy"] is False


@pytest.mark.asyncio
async def test_telegram_poller_conflict_releases_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telegram_poller.cfg, "TELEGRAM_POLLER_LEASE_ENABLED", True)
    monkeypatch.setattr(telegram_poller.cfg, "TELEGRAM_POLLER_LEASE_RENEW_INTERVAL_SECONDS", 3600)
    client = _StubControlPlaneClient(
        acquire_payload={
            "acquired": True,
            "owner_id": "gw-local",
            "held": True,
        },
        renew_payload={"renewed": True, "owner_id": "gw-local", "held": True},
    )

    controller = telegram_poller.TelegramPollerLeaseController(
        gateway_id="gw-local",
        bot_token="123:test-token",
        client=client,
    )
    assert await controller.acquire() is True
    controller.mark_running()

    await controller.handle_conflict(detail="Conflict: terminated by other getUpdates request")
    await asyncio.sleep(0)

    status = telegram_poller.get_telegram_poller_status()
    assert status["telegram_poller_state"] == "conflict"
    assert status["telegram_poller_conflict_count"] >= 1
    assert client.release_calls
