"""Enforce engineering policy for documentation, testing, and tracing evidence."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
POLICY_MODE_BASELINE = "baseline"
POLICY_MODE_STRICT = "strict"

REQUIRED_DOC_HEADINGS: dict[str, list[str]] = {
    "docs/INDEX.md": [
        "# SKYNET Documentation Index",
        "## Reading Order",
        "## Authoritative Sources",
    ],
    "docs/ARCHITECTURE_MAP.md": [
        "# SKYNET Architecture Map",
        "## Runtime Topology",
        "## Entrypoints",
        "## Data Stores",
        "## Ownership Boundaries",
    ],
    "docs/IMPLEMENTATION_GUIDE.md": [
        "# SKYNET Implementation Guide",
        "## Control Plane Implementation",
        "## Gateway Implementation",
        "## Agent Implementation",
        "## Environment Precedence and Critical Flags",
        "## Action Surfaces",
        "## Safe-Edit Hotspots and High-Risk Files",
        "## Local Verification Commands",
    ],
    "docs/DEBUG_PLAYBOOK.md": [
        "# SKYNET Debug Playbook",
        "## Triage Workflow",
        "## Connectivity Failures",
        "## Task Lock and Stale Claim Failures",
        "## Idempotency Anomalies",
        "## Provider Routing Failures",
        "## Log and Trace Collection",
        "## Incident Checklist",
    ],
    "docs/KNOWN_DRIFT_AND_TEST_MATRIX.md": [
        "# Known Drift and Test Matrix",
        "## Current Baseline",
        "## Known Drift",
        "## Authoritative Test Matrix",
        "## Legacy or Optional Suites",
    ],
    "docs/ENGINEERING_POLICY.md": [
        "# Engineering Policy",
        "## Documentation Requirements",
        "## Configuration Source of Truth",
        "## Testing Requirements",
        "## Tracing Requirements",
        "## Merge and Handoff Evidence",
        "## Policy Checklist",
    ],
}

REQUIRED_HANDOFF_SECTIONS = [
    "## Test Results",
    "## Trace Evidence",
    "## Documentation Updates",
    "## Policy Checklist",
]

CODE_PATH_PREFIXES = ("skynet/", "openclaw-gateway/", "openclaw-agent/", "scripts/")
TEST_EVIDENCE_PATTERN = re.compile(r"(python|py)\s+-m\s+pytest|make\s+test|make\s+smoke", re.IGNORECASE)
TRACE_MARKER_PATTERN = re.compile(
    r"request_id|task_id|claim_token|audit\.jsonl|skynet\.trace\.log|/v1/events",
    re.IGNORECASE,
)
TEMPLATE_DOCSTRING_BLOCK_PATTERN = re.compile(r'(?ms)^[ \t]*"""\n.*?^[ \t]*"""[ \t]*$')
TEMPLATE_DOCSTRING_MARKERS = ("Purpose:", "How it works:", "Why this exists:")
TEMPLATE_DOCSTRING_ALLOWLIST: set[str] = set()


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _extract_section(markdown: str, heading: str) -> str:
    lines = markdown.splitlines()
    start_idx = -1
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            start_idx = idx + 1
            break

    if start_idx == -1:
        return ""

    body: list[str] = []
    for line in lines[start_idx:]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body).strip()


def _is_code_path(rel_path: str) -> bool:
    return rel_path.startswith(CODE_PATH_PREFIXES)


def _missing_doc_headings(repo_root: Path) -> list[str]:
    violations: list[str] = []
    for rel_path, headings in REQUIRED_DOC_HEADINGS.items():
        path = repo_root / rel_path
        if not path.exists():
            violations.append(f"Missing required policy document: {rel_path}")
            continue
        text = _read_text(path)
        for heading in headings:
            if heading not in text:
                violations.append(f"{rel_path}: missing required heading '{heading}'")
    return violations


def _template_docstring_violations(repo_root: Path) -> list[str]:
    violations: list[str] = []
    for prefix in CODE_PATH_PREFIXES:
        root = repo_root / prefix
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            rel_path = path.relative_to(repo_root).as_posix()
            if "/tests/" in f"/{rel_path}/":
                continue
            if rel_path in TEMPLATE_DOCSTRING_ALLOWLIST:
                continue
            text = _read_text(path)
            has_template_docstring = any(
                any(marker in block.group(0) for marker in TEMPLATE_DOCSTRING_MARKERS)
                for block in TEMPLATE_DOCSTRING_BLOCK_PATTERN.finditer(text)
            )
            if has_template_docstring:
                violations.append(
                    f"{rel_path}: contains template-generated docstring markers "
                    "(Purpose/How it works/Why this exists)."
                )
    return violations


def evaluate_policy(
    repo_root: Path,
    changed_files: Sequence[str],
    *,
    strict: bool = True,
) -> list[str]:
    violations = _missing_doc_headings(repo_root)
    violations.extend(_template_docstring_violations(repo_root))

    changed = [p.replace("\\", "/").strip() for p in changed_files if p.strip()]
    code_paths_changed = any(_is_code_path(path) for path in changed)

    if not strict or not code_paths_changed:
        return violations

    handoff_rel = "docs/AGENT_HANDOFF.md"
    handoff_path = repo_root / handoff_rel
    handoff_text = _read_text(handoff_path)

    if handoff_rel not in changed:
        violations.append(
            "Code paths changed but docs/AGENT_HANDOFF.md is not included in the change set."
        )

    if not handoff_text:
        violations.append("Missing docs/AGENT_HANDOFF.md content.")
        return violations

    missing_sections = [
        section for section in REQUIRED_HANDOFF_SECTIONS if not _extract_section(handoff_text, section)
    ]
    for section in missing_sections:
        violations.append(f"docs/AGENT_HANDOFF.md: section '{section}' is missing or empty.")

    test_section = _extract_section(handoff_text, "## Test Results")
    has_test_evidence = bool(TEST_EVIDENCE_PATTERN.search(test_section))
    has_no_test_justification = "NoTestJustification" in test_section
    if test_section and not has_test_evidence and not has_no_test_justification:
        violations.append(
            "docs/AGENT_HANDOFF.md: '## Test Results' must contain test command evidence or "
            "'NoTestJustification'."
        )

    trace_section = _extract_section(handoff_text, "## Trace Evidence")
    if trace_section and not TRACE_MARKER_PATTERN.search(trace_section):
        violations.append(
            "docs/AGENT_HANDOFF.md: '## Trace Evidence' must include at least one marker "
            "(request_id, task_id, claim_token, audit.jsonl, skynet.trace.log, /v1/events)."
        )

    return violations


def collect_changed_files(repo_root: Path, base_ref: str, head_ref: str) -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", base_ref, head_ref],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    changed: set[str] = set()
    if proc.returncode == 0:
        changed.update(line.strip() for line in proc.stdout.splitlines() if line.strip())
    else:
        # Fallback for shallow clones or repositories where base_ref is unavailable.
        fallback = subprocess.run(
            ["git", "show", "--name-only", "--pretty=format:", head_ref],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if fallback.returncode != 0:
            stderr = proc.stderr.strip() or fallback.stderr.strip() or "changed-file detection failed"
            raise RuntimeError(stderr)
        changed.update(line.strip() for line in fallback.stdout.splitlines() if line.strip())

    # Include local working-tree changes so local policy runs work before commit.
    for extra_cmd in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--name-only", "--cached"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        extra = subprocess.run(
            extra_cmd,
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if extra.returncode != 0:
            stderr = extra.stderr.strip() or f"{' '.join(extra_cmd)} failed"
            raise RuntimeError(stderr)
        changed.update(line.strip() for line in extra.stdout.splitlines() if line.strip())

    return sorted(changed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check engineering policy compliance.")
    parser.add_argument("--base-ref", default="HEAD~1", help="Base git ref for changed-file detection.")
    parser.add_argument("--head-ref", default="HEAD", help="Head git ref for changed-file detection.")
    parser.add_argument(
        "--mode",
        choices=(POLICY_MODE_BASELINE, POLICY_MODE_STRICT),
        default=POLICY_MODE_BASELINE,
        help="baseline checks docs/docstrings only; strict also enforces handoff evidence for changed code paths.",
    )
    parser.add_argument(
        "--strict",
        action="store_const",
        const=POLICY_MODE_STRICT,
        dest="mode",
        help="Compatibility alias for --mode strict.",
    )
    parser.add_argument(
        "--baseline",
        action="store_const",
        const=POLICY_MODE_BASELINE,
        dest="mode",
        help="Compatibility alias for --mode baseline.",
    )
    args = parser.parse_args()

    strict = args.mode == POLICY_MODE_STRICT
    changed_files: list[str] = []
    if strict:
        try:
            changed_files = collect_changed_files(ROOT, args.base_ref, args.head_ref)
        except RuntimeError as exc:
            print(f"Engineering policy check failed to collect changed files: {exc}")
            return 1

    violations = evaluate_policy(ROOT, changed_files, strict=strict)
    if violations:
        print("Engineering policy violations detected:")
        for violation in violations:
            print(f"- {violation}")
        return 1

    print(f"Engineering policy check passed ({args.mode}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
