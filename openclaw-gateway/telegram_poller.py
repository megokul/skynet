from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Any

import gateway_config as cfg

from control_plane_client import ControlPlaneClient


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_poller_state: dict[str, Any] = {
    "state": "inactive",
    "lease_name": "",
    "lease_owner": "",
    "lease_enabled": False,
    "lock_healthy": False,
    "last_conflict_at": "",
    "conflict_count": 0,
    "last_error": "",
    "last_event_at": "",
    "gateway_id": "",
}


def _update_state(**fields: Any) -> None:
    _poller_state.update(fields)
    _poller_state["last_event_at"] = _utc_now_iso()


def get_telegram_poller_status() -> dict[str, Any]:
    return {
        "telegram_poller_state": str(_poller_state.get("state") or "inactive"),
        "telegram_poller_lease_name": str(_poller_state.get("lease_name") or ""),
        "telegram_poller_lease_owner": str(_poller_state.get("lease_owner") or ""),
        "telegram_poller_last_conflict_at": str(_poller_state.get("last_conflict_at") or ""),
        "telegram_poller_conflict_count": int(_poller_state.get("conflict_count", 0) or 0),
        "telegram_poller_lock_healthy": bool(_poller_state.get("lock_healthy", False)),
        "telegram_poller_last_error": str(_poller_state.get("last_error") or ""),
        "telegram_poller_lease_enabled": bool(_poller_state.get("lease_enabled", False)),
        "telegram_poller_gateway_id": str(_poller_state.get("gateway_id") or ""),
    }


def build_telegram_poller_lease_name(bot_token: str) -> str:
    digest = hashlib.sha1(str(bot_token or "").encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"telegram-poller:{digest}"


class TelegramPollerLeaseController:
    """Coordinates the Telegram long-poller singleton lease via the control plane."""

    def __init__(
        self,
        *,
        gateway_id: str | None = None,
        bot_token: str | None = None,
        client: ControlPlaneClient | None = None,
    ) -> None:
        self.gateway_id = str(gateway_id or cfg.GATEWAY_ID or "openclaw").strip() or "openclaw"
        self.bot_token = str(bot_token or cfg.TELEGRAM_BOT_TOKEN or "").strip()
        self.lease_enabled = bool(getattr(cfg, "TELEGRAM_POLLER_LEASE_ENABLED", True))
        self.lease_ttl_seconds = max(
            15,
            int(getattr(cfg, "TELEGRAM_POLLER_LEASE_TTL_SECONDS", 60) or 60),
        )
        self.renew_interval_seconds = max(
            5,
            int(getattr(cfg, "TELEGRAM_POLLER_LEASE_RENEW_INTERVAL_SECONDS", 20) or 20),
        )
        self.lease_name = build_telegram_poller_lease_name(self.bot_token)
        self.client = client or ControlPlaneClient(timeout_seconds=10)
        self._renew_task: asyncio.Task[Any] | None = None
        self._stopped = False
        _update_state(
            state="inactive",
            lease_name=self.lease_name,
            lease_owner="",
            lease_enabled=self.lease_enabled,
            lock_healthy=not self.lease_enabled,
            last_error="",
            gateway_id=self.gateway_id,
        )

    async def acquire(self) -> bool:
        if not self.lease_enabled:
            _update_state(
                state="disabled",
                lease_owner=self.gateway_id,
                lock_healthy=True,
            )
            return True
        _update_state(state="acquiring", lease_owner="", lock_healthy=False, last_error="")
        try:
            payload = await self.client.acquire_lease(
                self.lease_name,
                owner_id=self.gateway_id,
                ttl_seconds=self.lease_ttl_seconds,
            )
        except Exception as exc:
            _update_state(
                state="error",
                lock_healthy=False,
                last_error=f"{type(exc).__name__}: {exc}",
            )
            return False
        owner_id = str(payload.get("owner_id") or "")
        acquired = bool(payload.get("acquired", False))
        _update_state(
            state="leased" if acquired else "blocked",
            lease_owner=owner_id,
            lock_healthy=acquired and owner_id == self.gateway_id,
            last_error="" if acquired else "foreign_lease_active",
        )
        if not acquired:
            return False
        self._renew_task = asyncio.create_task(self._renew_loop(), name="telegram-poller-lease-renew")
        return True

    def mark_running(self) -> None:
        _update_state(
            state="running",
            lease_owner=self.gateway_id,
            lock_healthy=True,
            last_error="",
        )

    async def handle_conflict(self, *, detail: str) -> None:
        _update_state(
            state="conflict",
            lease_owner="",
            lock_healthy=False,
            last_conflict_at=_utc_now_iso(),
            conflict_count=int(_poller_state.get("conflict_count", 0) or 0) + 1,
            last_error=detail[:500],
        )
        await self.release()

    async def release(self) -> None:
        self._stopped = True
        if self._renew_task is not None:
            self._renew_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._renew_task
            self._renew_task = None
        if not self.lease_enabled:
            return
        try:
            await self.client.release_lease(self.lease_name, owner_id=self.gateway_id)
        except Exception as exc:
            _update_state(
                state="error",
                lock_healthy=False,
                last_error=f"{type(exc).__name__}: {exc}",
            )
            return
        if str(_poller_state.get("state") or "") != "conflict":
            _update_state(
                state="released",
                lease_owner="",
                lock_healthy=False,
            )

    async def _renew_loop(self) -> None:
        try:
            while not self._stopped:
                await asyncio.sleep(self.renew_interval_seconds)
                payload = await self.client.renew_lease(
                    self.lease_name,
                    owner_id=self.gateway_id,
                    ttl_seconds=self.lease_ttl_seconds,
                )
                renewed = bool(payload.get("renewed", False))
                owner_id = str(payload.get("owner_id") or "")
                if not renewed or owner_id != self.gateway_id:
                    _update_state(
                        state="blocked",
                        lease_owner=owner_id,
                        lock_healthy=False,
                        last_error="lease_renew_rejected",
                    )
                    return
                _update_state(
                    state="running" if str(_poller_state.get("state") or "") == "running" else "leased",
                    lease_owner=owner_id,
                    lock_healthy=True,
                    last_error="",
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _update_state(
                state="error",
                lock_healthy=False,
                last_error=f"{type(exc).__name__}: {exc}",
            )


import contextlib  # noqa: E402  # imported late to keep module header compact
