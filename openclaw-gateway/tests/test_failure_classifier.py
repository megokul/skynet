from orchestration.failure import (
    FAIL_CONTRACT,
    FAIL_ENVIRONMENT,
    FAIL_GENERATION,
    FAIL_STRICT_GATE,
    FAIL_TEST,
    classify_gate_failure,
    classify_generation_error,
    is_repairable,
)


def test_classify_generation_error_env_signatures():
    assert classify_generation_error("SSH_INFRA_UNREACHABLE host") == FAIL_ENVIRONMENT
    assert classify_generation_error("write blocked in sandbox") == FAIL_ENVIRONMENT
    assert classify_generation_error("model returned no files") == FAIL_GENERATION


def test_classify_gate_failure_mapping():
    assert (
        classify_gate_failure(failed_gate_names=["run_contract"], error_message="bad manifest")
        == FAIL_CONTRACT
    )
    assert classify_gate_failure(failed_gate_names=["tests"], error_message="pytest failed") == FAIL_TEST
    assert (
        classify_gate_failure(
            failed_gate_names=["smoke"],
            error_message="ssh connection reset",
        )
        == FAIL_ENVIRONMENT
    )
    assert classify_gate_failure(failed_gate_names=["lint"], error_message="ruff") == FAIL_STRICT_GATE


def test_is_repairable_matrix():
    assert is_repairable(FAIL_GENERATION) is True
    assert is_repairable(FAIL_CONTRACT) is True
    assert is_repairable(FAIL_TEST) is True
    assert is_repairable(FAIL_STRICT_GATE) is True
    assert is_repairable(FAIL_ENVIRONMENT) is False
