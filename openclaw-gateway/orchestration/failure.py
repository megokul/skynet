from __future__ import annotations

FAIL_GENERATION = "GENERATION_FAILED"
FAIL_CONTRACT = "CONTRACT_FAILED"
FAIL_STRICT_GATE = "STRICT_GATE_FAILED"
FAIL_TEST = "TEST_FAILED"
FAIL_ENVIRONMENT = "ENVIRONMENT_FAILED"
FAIL_CRITIC_PARSE = "CRITIC_PARSE_FAILED"
FAIL_DEADLOCK = "DEADLOCK_DETECTED"
FAIL_BUDGET = "BUDGET_EXCEEDED"


def classify_generation_error(detail: str) -> str:
    text = (detail or "").strip().lower()
    if not text:
        return FAIL_GENERATION
    if "ssh_inf" in text or "timeout" in text or "connection" in text or "infra" in text:
        return FAIL_ENVIRONMENT
    if "code_write_blocked" in text or "write blocked" in text:
        return FAIL_ENVIRONMENT
    return FAIL_GENERATION


def classify_gate_failure(*, failed_gate_names: list[str], error_message: str) -> str:
    failed = {str(name).strip().lower() for name in (failed_gate_names or []) if str(name).strip()}
    text = (error_message or "").strip().lower()
    if "infra" in text:
        return FAIL_ENVIRONMENT
    if "run_contract" in failed:
        return FAIL_CONTRACT
    if "tests" in failed:
        return FAIL_TEST
    if "smoke" in failed and ("ssh" in text or "connection" in text):
        return FAIL_ENVIRONMENT
    return FAIL_STRICT_GATE


def is_repairable(failure_type: str) -> bool:
    normalized = (failure_type or "").strip().upper()
    return normalized in {FAIL_GENERATION, FAIL_CONTRACT, FAIL_STRICT_GATE, FAIL_TEST}
