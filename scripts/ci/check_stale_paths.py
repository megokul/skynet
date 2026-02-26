"""Fail if operational docs/configs reference stale root script paths."""

from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]

# Keep this scoped to operational files; historical logs are intentionally excluded.
FILES_TO_CHECK = [
    "README.md",
    "AGENT_GUIDE.md",
    "Makefile",
    "scripts/manual/README.md",
]

STALE_PATTERNS = [
    r"python\s+run_api\.py\b",
    r"`run_api\.py`",
    r"python\s+test_api\.py\b",
    r"`test_api\.py`",
    r"python\s+test_e2e_integration\.py\b",
    r"`test_e2e_integration\.py`",
    r"python\s+test_skynet_delegate\.py\b",
    r"`test_skynet_delegate\.py`",
    r"python\s+test_[a-zA-Z0-9_]+\.py\b",
]

# Allowed modern paths/commands that include old leaf filenames.
ALLOWLIST_SNIPPETS = [
    "scripts/dev/run_api.py",
    "scripts/manual/check_api.py",
    "scripts/manual/check_e2e_integration.py",
    "scripts/manual/check_skynet_delegate.py",
    "tests/test_",
]


def main() -> int:
    """
    Main.
    
    Purpose:
    - Implement `main` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `int` when available; otherwise side effects only.
    """

    violations: list[tuple[str, int, str]] = []
    combined = re.compile("|".join(f"(?:{p})" for p in STALE_PATTERNS))

    for rel_path in FILES_TO_CHECK:
        path = ROOT / rel_path
        if not path.exists():
            continue

        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not combined.search(line):
                continue
            if any(snippet in line for snippet in ALLOWLIST_SNIPPETS):
                continue
            violations.append((rel_path, lineno, line.strip()))

    if not violations:
        print("No stale path references found.")
        return 0

    print("Stale path references detected:")
    for rel_path, lineno, line in violations:
        safe_line = line.encode("ascii", errors="replace").decode("ascii")
        print(f"- {rel_path}:{lineno}: {safe_line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
