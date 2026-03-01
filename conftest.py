"""
Root-level conftest for the SKYNET monorepo.

Solves the 'config' module collision between openclaw-gateway and openclaw-agent:
both sub-packages have a top-level config.py; when pytest collects both test
dirs in a single process they share sys.modules, so the last-inserted sys.path
entry wins — and the wrong config.py gets cached.

Fix: hook pytest_collect_file (called just before each test *file* is imported)
to put the correct package root at the front of sys.path AND evict any stale
'config' from sys.modules.  This guarantees that gateway test files always see
openclaw-gateway/config.py and agent test files always see openclaw-agent/config.py.
"""

from __future__ import annotations

import os
import sys

_HERE         = os.path.dirname(os.path.abspath(__file__))
_GATEWAY_ROOT = os.path.normpath(os.path.join(_HERE, "openclaw-gateway"))
_AGENT_ROOT   = os.path.normpath(os.path.join(_HERE, "openclaw-agent"))


def _move_to_front(root: str) -> None:
    """Evict stale 'config', remove root from sys.path, re-insert at index 0."""
    sys.modules.pop("config", None)
    while root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)


def pytest_collect_file(parent, file_path):  # noqa: ANN001
    """Switch sys.path before each test file is imported during collection."""
    path_str = str(file_path)
    if "openclaw-gateway" in path_str:
        _move_to_front(_GATEWAY_ROOT)
    elif "openclaw-agent" in path_str:
        _move_to_front(_AGENT_ROOT)
    # Return None so pytest uses its default collector for this file.
    return None
