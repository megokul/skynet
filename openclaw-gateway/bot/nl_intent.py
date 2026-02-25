"""
bot/nl_intent.py -- Project reference resolution.

The waterfall intent-extraction pipeline has been replaced by an LLM-first
approach where the LLM calls project management tools directly. This module
now contains only the utilities that remain needed by commands.py.
"""
from __future__ import annotations

import logging
import re

from . import state
from .helpers import _clean_entity, _norm_project, _project_display

logger = logging.getLogger("skynet.telegram")


_NEW_PROJECT_RE = re.compile(
    r"\b(?:start|create|make|begin|initiate)\b.{0,35}\bproject\b"
    r"|\bnew\s+(?:project|app|application)\b"
    r"|\bproject\b.{0,20}\b(?:start|create|make|begin)\b",
    re.IGNORECASE,
)


def _is_new_project_intent(text: str) -> bool:
    """Return True if the message strongly signals intent to START a brand-new project."""
    return bool(_NEW_PROJECT_RE.search((text or "").strip()))


async def _resolve_project(
    reference: str | None = None,
    *,
    active_project_id: str | None = None,
) -> tuple[dict | None, str | None]:
    """Resolve a natural-language project reference to a concrete project."""
    if not state._project_manager:
        return None, "Project manager is not initialized."

    projects = await state._project_manager.list_projects()
    if not projects:
        return None, "No projects exist yet. Tell me the project name and I will create it."

    if reference:
        ref = _clean_entity(reference)
        ref_norm = _norm_project(ref)
        if not ref_norm:
            reference = None
        else:
            scored: list[tuple[int, dict]] = []
            for project in projects:
                display = _project_display(project)
                name = str(project.get("name", ""))
                d_norm = _norm_project(display)
                n_norm = _norm_project(name)
                if ref_norm in {d_norm, n_norm}:
                    scored.append((100, project))
                elif d_norm.startswith(ref_norm) or n_norm.startswith(ref_norm):
                    scored.append((80, project))
                elif ref_norm in d_norm or ref_norm in n_norm:
                    scored.append((60, project))

            if not scored:
                return None, f"I couldn't find a project named '{ref}'."

            scored.sort(key=lambda item: item[0], reverse=True)
            top_score = scored[0][0]
            top = [p for score, p in scored if score == top_score]
            if len(top) > 1:
                choices = ", ".join(_project_display(p) for p in top[:4])
                return None, f"I found multiple matches: {choices}. Tell me the exact name."

            return top[0], None

    # No explicit reference: use recent context first.
    if active_project_id:
        for project in projects:
            if project["id"] == active_project_id:
                return project, None

    ideation = [p for p in projects if p.get("status") == "ideation"]
    if len(ideation) == 1:
        return ideation[0], None

    if len(projects) == 1:
        return projects[0], None

    active_statuses = {"planning", "approved", "coding", "testing", "paused"}
    active = [p for p in projects if p.get("status") in active_statuses]
    if len(active) == 1:
        return active[0], None

    choices = ", ".join(_project_display(p) for p in projects[:5])
    return None, f"Which project do you mean? I have: {choices}."
