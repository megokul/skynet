from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable


def resolve_targets(log_dir: str, mirror_log_dir: str | None, filename: str) -> list[Path]:
    targets: list[Path] = []
    for directory in iter_dirs(log_dir, mirror_log_dir):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            targets.append(directory / filename)
        except Exception:
            logging.getLogger("skynet").exception("Failed preparing log directory: %s", directory)
    return targets


def iter_dirs(primary: str, mirror: str | None) -> Iterable[Path]:
    seen: set[str] = set()
    for raw in (primary, mirror):
        value = (raw or "").strip()
        if not value:
            continue
        normalized = str(Path(value))
        if normalized in seen:
            continue
        seen.add(normalized)
        yield Path(value)


def join_windows_path(base: str, filename: str) -> str:
    clean_base = (base or "").strip().rstrip("\\/")
    if not clean_base:
        return filename
    return f"{clean_base}\\{filename}"


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
