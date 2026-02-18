"""Provider config resolution tests for API startup."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.api.main import _get_gateway_urls_from_env


def test_get_gateway_urls_default(monkeypatch) -> None:
    monkeypatch.delenv("OPENCLAW_GATEWAY_URLS", raising=False)
    monkeypatch.delenv("OPENCLAW_GATEWAY_URL", raising=False)
    urls = _get_gateway_urls_from_env()
    assert urls == ["http://127.0.0.1:8766"]


def test_get_gateway_urls_respects_configured_list(monkeypatch) -> None:
    monkeypatch.setenv(
        "OPENCLAW_GATEWAY_URLS",
        "http://gateway-a:8766,http://gateway-b:8766",
    )
    urls = _get_gateway_urls_from_env()
    assert urls == ["http://gateway-a:8766", "http://gateway-b:8766"]


def test_get_gateway_urls_single_fallback(monkeypatch) -> None:
    monkeypatch.delenv("OPENCLAW_GATEWAY_URLS", raising=False)
    monkeypatch.setenv("OPENCLAW_GATEWAY_URL", "http://example-gateway:9000")
    urls = _get_gateway_urls_from_env()
    assert urls == ["http://example-gateway:9000"]
