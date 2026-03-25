from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class SSHHealthTracker:
    circuit_breaker_seconds: int
    capacity_backoff_seconds: int
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _failure_streak: int = 0
    _last_error_category: str = ""
    _circuit_open_until: float = 0.0

    def classify_error(self, detail: str) -> str:
        text = (detail or "").strip().lower()
        if not text:
            return "unknown"
        if (
            "maxstartups" in text
            or "exceeded maxstartups" in text
            or "concurrency limit reached" in text
            or "max_parallel" in text
        ):
            return "capacity"
        if (
            "permission denied" in text
            or "authentication failed" in text
            or "no authentication methods available" in text
        ):
            return "auth"
        if (
            "error reading ssh protocol banner" in text
            or "no existing session" in text
            or "ssh protocol banner" in text
        ):
            return "banner"
        if "timed out" in text or "timeout" in text:
            return "timeout"
        if (
            "connection refused" in text
            or "name or service not known" in text
            or "could not resolve hostname" in text
            or "network is unreachable" in text
            or "no route to host" in text
            or "host unreachable" in text
            or "unable to connect to port" in text
        ):
            return "unreachable"
        return "unknown"

    def retry_delay_for_category(self, category: str, attempt: int) -> int:
        if category == "capacity":
            return self.capacity_backoff_seconds
        if category == "banner":
            return 5 if attempt <= 1 else 10
        if category in {"timeout", "unreachable"}:
            return 2 if attempt <= 1 else 4
        return min(8, max(1, attempt * 2))

    def record_success(self) -> None:
        with self._lock:
            self._failure_streak = 0
            self._last_error_category = ""
            self._circuit_open_until = 0.0

    def record_failure(self, category: str) -> None:
        now = time.time()
        with self._lock:
            self._failure_streak += 1
            self._last_error_category = category or "unknown"
            if self.circuit_breaker_seconds <= 0:
                return
            should_open = False
            if category == "capacity" and self._failure_streak >= 2:
                should_open = True
            elif category in {"banner", "timeout"} and self._failure_streak >= 3:
                should_open = True
            if should_open:
                self._circuit_open_until = max(
                    self._circuit_open_until,
                    now + float(self.circuit_breaker_seconds),
                )

    def circuit_remaining_seconds(self) -> int:
        now = time.time()
        with self._lock:
            if self._circuit_open_until <= now:
                self._circuit_open_until = 0.0
                return 0
            return int(max(1.0, self._circuit_open_until - now))

    def diagnostics(self, *, configured: bool, endpoint: str, healthy: bool) -> dict[str, int | str | bool]:
        remaining = self.circuit_remaining_seconds()
        with self._lock:
            category = self._last_error_category
            streak = int(self._failure_streak)
            open_until = int(self._circuit_open_until) if remaining > 0 else 0
        return {
            "ssh_health_ok": bool(configured and healthy and remaining <= 0),
            "ssh_error_category": category,
            "ssh_failure_streak": streak,
            "ssh_circuit_open_until": open_until,
            "ssh_endpoint": endpoint,
        }
