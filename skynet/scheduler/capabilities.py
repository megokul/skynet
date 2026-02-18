"""
Provider Capabilities — Capability matrix for execution providers.

Defines what each provider can do and matches them to task requirements.
"""

from __future__ import annotations

from typing import Set

# ============================================================================
# Provider Capability Matrix
# ============================================================================

PROVIDER_CAPABILITIES: dict[str, Set[str]] = {
    # Mock provider - supports all actions for testing
    "mock": {
        "git_status",
        "git_clone",
        "git_commit",
        "git_push",
        "run_tests",
        "build",
        "deploy_staging",
        "deploy_prod",
        "file_ops",
        "list_directory",
        "execute_command",
        "docker_build",
        "docker_run",
        "ssh_execute",
        "all",  # Special capability indicating universal support
    },
    # Local provider - runs on current machine
    "local": {
        "git_status",
        "git_clone",
        "git_commit",
        "git_push",
        "run_tests",
        "build",
        "file_ops",
        "list_directory",
        "execute_command",
    },
    # Docker provider - containerized execution
    "docker": {
        "git_status",
        "git_clone",
        "git_commit",
        "run_tests",
        "build",
        "file_ops",
        "list_directory",
        "execute_command",
        "docker_build",
        "docker_run",
        "isolation",  # Special capability: provides process isolation
    },
    # SSH provider - remote execution
    "ssh": {
        "git_status",
        "git_clone",
        "git_commit",
        "git_push",
        "run_tests",
        "build",
        "deploy_staging",
        "deploy_prod",
        "file_ops",
        "list_directory",
        "execute_command",
        "ssh_execute",
        "remote",  # Special capability: remote execution
    },
    # OpenClaw/Chathan provider - full AI-powered execution
    "chathan": {
        "all",  # Can handle any task type via subagents
        "ai_powered",
        "multi_step",
        "complex_reasoning",
    },
    "openclaw": {  # Alias for chathan
        "all",
        "ai_powered",
        "multi_step",
        "complex_reasoning",
    },
}


# ============================================================================
# Capability Checking Functions
# ============================================================================


def check_capability(provider_name: str, required_capability: str) -> bool:
    """
    Check if a provider has a specific capability.

    Args:
        provider_name: Name of the provider
        required_capability: Capability to check for

    Returns:
        True if provider has capability, False otherwise
    """
    if provider_name not in PROVIDER_CAPABILITIES:
        return False

    capabilities = PROVIDER_CAPABILITIES[provider_name]

    # Special case: "all" capability means provider supports everything
    if "all" in capabilities:
        return True

    return required_capability in capabilities


def get_matching_providers(required_capabilities: list[str]) -> list[str]:
    """
    Get all providers that match a set of required capabilities.

    Args:
        required_capabilities: List of required capabilities

    Returns:
        List of provider names that have all required capabilities
    """
    matching = []

    for provider_name, capabilities in PROVIDER_CAPABILITIES.items():
        # Check if provider has all required capabilities
        if "all" in capabilities:
            # Universal provider
            matching.append(provider_name)
        elif all(cap in capabilities for cap in required_capabilities):
            # Provider has all specific capabilities
            matching.append(provider_name)

    return matching


def calculate_capability_match(
    provider_name: str, required_capabilities: list[str]
) -> float:
    """
    Calculate capability match score (0.0 to 1.0).

    Args:
        provider_name: Name of the provider
        required_capabilities: List of required capabilities

    Returns:
        Match score: 1.0 = perfect match, 0.0 = no match
    """
    if not required_capabilities:
        return 1.0  # No requirements = perfect match

    if provider_name not in PROVIDER_CAPABILITIES:
        return 0.0  # Unknown provider

    capabilities = PROVIDER_CAPABILITIES[provider_name]

    # Special case: "all" capability
    if "all" in capabilities:
        return 1.0

    # Count how many required capabilities are supported
    matched = sum(1 for cap in required_capabilities if cap in capabilities)

    # Return percentage match
    return matched / len(required_capabilities)


def extract_required_capabilities(execution_spec: dict) -> list[str]:
    """
    Extract required capabilities from execution spec.

    Analyzes the actions in execution spec to determine what capabilities
    are needed.

    Args:
        execution_spec: Execution specification with steps/actions

    Returns:
        List of required capability names
    """
    required = set()

    # Get actions from execution spec (supports both formats)
    actions = execution_spec.get("actions", [])
    if not actions:
        steps = execution_spec.get("steps", [])
        actions = [step.get("action") for step in steps if isinstance(step, dict)]

    # Map actions to capabilities
    action_capability_map = {
        "git_status": "git_status",
        "git_clone": "git_clone",
        "git_commit": "git_commit",
        "git_push": "git_push",
        "run_tests": "run_tests",
        "build": "build",
        "deploy_staging": "deploy_staging",
        "deploy_prod": "deploy_prod",
        "list_directory": "list_directory",
        "execute_command": "execute_command",
        "docker_build": "docker_build",
        "docker_run": "docker_run",
        "ssh_execute": "ssh_execute",
    }

    for action in actions:
        if isinstance(action, str):
            # Direct action name
            capability = action_capability_map.get(action, action)
            required.add(capability)
        elif isinstance(action, dict):
            # Action dict with "action" field
            action_name = action.get("action", "")
            capability = action_capability_map.get(action_name, action_name)
            if capability:
                required.add(capability)

    return list(required)
