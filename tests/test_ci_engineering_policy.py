"""Unit tests for scripts/ci/check_engineering_policy.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_policy_module():
    repo_root = Path(__file__).parent.parent
    module_path = repo_root / "scripts" / "ci" / "check_engineering_policy.py"
    spec = importlib.util.spec_from_file_location("check_engineering_policy", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load check_engineering_policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_required_docs(repo_root: Path, policy_module, *, remove_heading: tuple[str, str] | None = None) -> None:
    for rel_path, headings in policy_module.REQUIRED_DOC_HEADINGS.items():
        out_path = repo_root / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for heading in headings:
            if remove_heading and remove_heading == (rel_path, heading):
                continue
            lines.append(heading)
            lines.append("")
            lines.append(f"Body for {heading}.")
            lines.append("")
        out_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _write_handoff(
    repo_root: Path,
    *,
    test_results: str,
    trace_evidence: str,
    doc_updates: str = "- docs/INDEX.md",
    policy_checklist: str = "- [x] Policy enforced",
) -> None:
    handoff = repo_root / "docs" / "AGENT_HANDOFF.md"
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(
        (
            "# Agent Handoff\n\n"
            "## Test Results\n\n"
            f"{test_results}\n\n"
            "## Trace Evidence\n\n"
            f"{trace_evidence}\n\n"
            "## Documentation Updates\n\n"
            f"{doc_updates}\n\n"
            "## Policy Checklist\n\n"
            f"{policy_checklist}\n"
        ),
        encoding="utf-8",
    )


def test_required_doc_headings_fail_when_missing(tmp_path: Path) -> None:
    policy = _load_policy_module()
    remove = ("docs/INDEX.md", "## Authoritative Sources")
    _write_required_docs(tmp_path, policy, remove_heading=remove)

    violations = policy.evaluate_policy(tmp_path, [], strict=True)
    assert any("docs/INDEX.md" in v and "Authoritative Sources" in v for v in violations)


def test_code_change_without_handoff_change_fails(tmp_path: Path) -> None:
    policy = _load_policy_module()
    _write_required_docs(tmp_path, policy)
    _write_handoff(
        tmp_path,
        test_results="python -m pytest tests/test_api_lifespan.py -q",
        trace_evidence="request_id=abc123",
    )

    violations = policy.evaluate_policy(tmp_path, ["skynet/api/main.py"], strict=True)
    assert any("docs/AGENT_HANDOFF.md is not included" in v for v in violations)


def test_code_change_with_missing_trace_marker_fails(tmp_path: Path) -> None:
    policy = _load_policy_module()
    _write_required_docs(tmp_path, policy)
    _write_handoff(
        tmp_path,
        test_results="python -m pytest tests/test_api_lifespan.py -q",
        trace_evidence="Trace captured in logs but no identifiers recorded.",
    )

    violations = policy.evaluate_policy(
        tmp_path,
        ["skynet/api/routes.py", "docs/AGENT_HANDOFF.md"],
        strict=True,
    )
    assert any("Trace Evidence" in v for v in violations)


def test_docs_only_change_passes(tmp_path: Path) -> None:
    policy = _load_policy_module()
    _write_required_docs(tmp_path, policy)

    violations = policy.evaluate_policy(tmp_path, ["docs/INDEX.md"], strict=True)
    assert violations == []


def test_valid_evidence_passes_for_code_change(tmp_path: Path) -> None:
    policy = _load_policy_module()
    _write_required_docs(tmp_path, policy)
    _write_handoff(
        tmp_path,
        test_results=(
            "python -m pytest tests/test_api_lifespan.py tests/test_api_provider_config.py -q\n"
            "python -m pytest openclaw-gateway/tests -q"
        ),
        trace_evidence="task_id=task-123 claim_token=claim-xyz source=/v1/events",
    )

    violations = policy.evaluate_policy(
        tmp_path,
        ["openclaw-gateway/api.py", "docs/AGENT_HANDOFF.md"],
        strict=True,
    )
    assert violations == []
