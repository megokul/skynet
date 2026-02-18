"""Fail if SKYNET control-plane entrypoints violate OpenClaw/SKYNET boundaries."""

from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]


def _read(rel_path: str) -> str:
    path = ROOT / rel_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _line_for(content: str, pattern: str) -> int:
    match = re.search(pattern, content, flags=re.MULTILINE)
    if not match:
        return 0
    return content.count("\n", 0, match.start()) + 1


def main() -> int:
    violations: list[tuple[str, int, str]] = []

    forbidden_paths = [
        "skynet/ai",
        "skynet/archive",
        "skynet/chathan",
        "skynet/cognition",
        "skynet/core",
        "skynet/events",
        "skynet/execution",
        "skynet/gateway",
        "skynet/memory",
        "skynet/policy",
        "skynet/queue",
        "skynet/scheduler",
        "skynet/sentinel",
        "skynet/shared/settings.py",
        "skynet/shared/logging.py",
        "skynet/shared/errors.py",
        "skynet/shared/utils.py",
        "skynet/telegram",
    ]
    for rel_path in forbidden_paths:
        path = ROOT / rel_path
        if not path.exists():
            continue
        if path.is_file():
            violations.append((rel_path, 1, "Forbidden runtime path exists"))
            continue
        if path.is_dir():
            py_files = [p for p in path.rglob("*.py") if "__pycache__" not in p.parts]
            if py_files:
                violations.append((rel_path, 1, "Forbidden runtime path exists"))

    file_checks = [
        {
            "path": "skynet/api/main.py",
            "forbidden": [
                r"skynet\.chathan\.providers\.",
            ],
        },
    ]

    for check in file_checks:
        path = check["path"]
        content = _read(path)
        if not content:
            violations.append((path, 1, "Missing required file for boundary checks"))
            continue
        for pattern in check["forbidden"]:
            lineno = _line_for(content, pattern)
            if lineno:
                violations.append((path, lineno, f"Forbidden runtime import pattern: {pattern}"))

    # Assert integration surfaces no longer call removed runtime endpoints.
    integration_surface_checks = [
        "openclaw-gateway/skills/skynet_delegate.py",
        "scripts/manual/check_api.py",
        "scripts/manual/check_e2e_integration.py",
        "scripts/manual/check_skynet_delegate.py",
    ]
    removed_endpoint_patterns = [
        r"/v1/report",
        r"/v1/execute",
        r"/v1/plan",
        r"/v1/policy/check",
        r"/v1/scheduler/diagnose",
        r"/v1/providers/health",
    ]
    for rel_path in integration_surface_checks:
        content = _read(rel_path)
        if not content:
            violations.append((rel_path, 1, "Missing required integration surface file"))
            continue
        for pattern in removed_endpoint_patterns:
            lineno = _line_for(content, pattern)
            if lineno:
                violations.append((rel_path, lineno, f"Removed endpoint still referenced: {pattern}"))

    routes_path = "skynet/api/routes.py"
    routes_content = _read(routes_path)
    if not routes_content:
        violations.append((routes_path, 1, "Missing routes module"))

    # Assert runtime-owned routes are not exposed by SKYNET.
    forbidden_route_patterns = [
        r"@router\.post\(\"/report\"",
        r"@router\.post\(\"/execute\"",
        r"@router\.post\(\"/plan\"",
        r"@router\.post\(\"/policy/check\"",
        r"@router\.post\(\"/scheduler/diagnose\"",
        r"@router\.get\(\"/providers/health\"",
        r"@router\.(get|post)\(\"/memory/",
    ]
    for pattern in forbidden_route_patterns:
        lineno = _line_for(routes_content, pattern)
        if lineno:
            violations.append((routes_path, lineno, f"Forbidden runtime route exposed: {pattern}"))

    # Assert control-plane contract endpoints exist.
    required_route_patterns = [
        (routes_path, r"@router\.post\(\"/register-gateway\""),
        (routes_path, r"@router\.post\(\"/register-worker\""),
        (routes_path, r"@router\.post\(\"/route-task\""),
        (routes_path, r"@router\.get\(\"/system-state\""),
    ]
    for rel_path, pattern in required_route_patterns:
        content = _read(rel_path)
        if not re.search(pattern, content, flags=re.MULTILINE):
            violations.append((rel_path, 1, f"Missing required control-plane route: {pattern}"))

    # Assert gateway-only provider env config is used in API startup.
    main_path = "skynet/api/main.py"
    main_content = _read(main_path)
    if "OPENCLAW_GATEWAY_URLS" not in main_content and "OPENCLAW_GATEWAY_URL" not in main_content:
        violations.append((main_path, 1, "Expected OpenClaw gateway env configuration in startup"))

    forbidden_main_patterns = [
        r"\bPolicyEngine\b",
        r"\bProviderScheduler\b",
        r"\bExecutionRouter\b",
        r"\bProviderMonitor\b",
        r"\bPlanner\b",
        r"GOOGLE_AI_API_KEY",
        r"memory_manager",
        r"vector_index",
        r"EventEngine",
    ]
    for pattern in forbidden_main_patterns:
        lineno = _line_for(main_content, pattern)
        if lineno:
            violations.append((main_path, lineno, f"Forbidden runtime component in API startup: {pattern}"))

    if not violations:
        print("Control-plane boundary check passed.")
        return 0

    print("Control-plane boundary violations detected:")
    for rel_path, lineno, reason in violations:
        print(f"- {rel_path}:{lineno}: {reason}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
