"""Package initializer for `openclaw-gateway/core/roles`.

Purpose:
- Mark this directory as an importable Python package.
- Provide one central location for package-level documentation.
- Clarify package boundaries and intended responsibilities.

How it works:
- Module import executes this file before submodule imports.
- The package may optionally expose convenience exports via __all__.

Why this exists:
- Keeps package structure explicit for contributors and tooling.
- Prevents ambiguity about where related runtime functionality lives."""

from core.roles.base import Role, RoleContext, RoleOutput
from core.roles.registry import RoleRegistry, build_default_registry

__all__ = [
    "Role",
    "RoleContext",
    "RoleOutput",
    "RoleRegistry",
    "build_default_registry",
]