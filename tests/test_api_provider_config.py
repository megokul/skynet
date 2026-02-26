"""Provider config resolution tests for API startup."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.api.main import _get_gateway_urls_from_env


def test_get_gateway_urls_default(monkeypatch) -> None:
    """
    Test scenario `test_get_gateway_urls_default`.
    
    Purpose:
    - Implement `test_get_gateway_urls_default` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `monkeypatch`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    monkeypatch.delenv("OPENCLAW_GATEWAY_URLS", raising=False)
    monkeypatch.delenv("OPENCLAW_GATEWAY_URL", raising=False)
    urls = _get_gateway_urls_from_env()
    assert urls == ["http://127.0.0.1:8766"]


def test_get_gateway_urls_respects_configured_list(monkeypatch) -> None:
    """
    Test scenario `test_get_gateway_urls_respects_configured_list`.
    
    Purpose:
    - Implement `test_get_gateway_urls_respects_configured_list` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `monkeypatch`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    monkeypatch.setenv(
        "OPENCLAW_GATEWAY_URLS",
        "http://gateway-a:8766,http://gateway-b:8766",
    )
    urls = _get_gateway_urls_from_env()
    assert urls == ["http://gateway-a:8766", "http://gateway-b:8766"]


def test_get_gateway_urls_single_fallback(monkeypatch) -> None:
    """
    Test scenario `test_get_gateway_urls_single_fallback`.
    
    Purpose:
    - Implement `test_get_gateway_urls_single_fallback` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `monkeypatch`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    monkeypatch.delenv("OPENCLAW_GATEWAY_URLS", raising=False)
    monkeypatch.setenv("OPENCLAW_GATEWAY_URL", "http://example-gateway:9000")
    urls = _get_gateway_urls_from_env()
    assert urls == ["http://example-gateway:9000"]
