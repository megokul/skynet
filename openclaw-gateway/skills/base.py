"""Base classes for OpenClaw gateway skills."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SkillContext:
    """Runtime context passed to every skill execution."""

    project_id: str
    project_path: str
    gateway_api_url: str
