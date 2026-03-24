from __future__ import annotations

import pytest

from orchestration.critic import is_blocking, normalize_severity, parse_critic_response


def test_normalize_severity_defaults_to_medium():
    assert normalize_severity("unknown") == "medium"
    assert normalize_severity("HIGH") == "high"


def test_parse_critic_response_json_block():
    payload = parse_critic_response(
        """```json
{"passed": false, "findings": [{"severity":"critical","code":"SEC-1","message":"x","files":["a.py"],"suggested_fix":"y"}]}
```"""
    )
    assert payload["passed"] is False
    assert payload["findings"][0]["severity"] == "critical"
    assert payload["findings"][0]["code"] == "SEC-1"


def test_parse_critic_response_invalid_json_raises():
    with pytest.raises(ValueError):
        parse_critic_response("the code needs significant rework in several areas")


def test_is_blocking_respects_threshold():
    findings = [
        {"severity": "medium"},
        {"severity": "high"},
    ]
    assert is_blocking(findings, threshold="high") is True
    assert is_blocking(findings, threshold="critical") is False

