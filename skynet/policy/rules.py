"""
SKYNET Policy Rules

Risk mappings and helper functions used by the policy engine.
"""

from __future__ import annotations


ACTION_RISK: dict[str, str] = {
    "file_read": "READ_ONLY",
    "list_directory": "READ_ONLY",
    "git_status": "READ_ONLY",
    "ollama_chat": "READ_ONLY",
    "run_tests": "READ_ONLY",
    "lint_project": "READ_ONLY",
    "start_dev_server": "READ_ONLY",
    "file_write": "WRITE",
    "create_directory": "WRITE",
    "git_commit": "WRITE",
    "git_init": "WRITE",
    "git_add_all": "WRITE",
    "install_dependencies": "WRITE",
    "build_project": "WRITE",
    "docker_build": "WRITE",
    "zip_project": "WRITE",
    "open_in_vscode": "WRITE",
    "git_push": "ADMIN",
    "gh_create_repo": "ADMIN",
    "docker_compose_up": "ADMIN",
    "close_app": "ADMIN",
}

BLOCKED_ACTIONS: set[str] = {
    "shell_exec",
    "format_disk",
    "modify_registry",
    "manage_users",
    "firewall_change",
    "download_exec",
    "eval_code",
}

_RISK_ORDER = {"READ_ONLY": 0, "WRITE": 1, "ADMIN": 2}


def classify_action_risk(action: str) -> str:
    """Classify one action name into risk level."""
    if action in BLOCKED_ACTIONS:
        return "BLOCKED"
    return ACTION_RISK.get(action, "ADMIN")


def risk_exceeds(action_risk: str, max_allowed: str) -> bool:
    """Return True when action risk is greater than allowed maximum."""
    return _RISK_ORDER.get(action_risk, 2) > _RISK_ORDER.get(max_allowed, 1)
