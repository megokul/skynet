from __future__ import annotations

import asyncio
import base64
import io
import os
import shutil
import zipfile
from typing import Any

from executor.action_support import require_param


def read_file_sync(filepath: str) -> str:
    with open(filepath, "r", encoding="utf-8", errors="replace") as handle:
        content = handle.read()
    if len(content) > 65536:
        return content[:65536] + "\n... (truncated at 64 KB)"
    return content


async def file_read(params: dict[str, Any]) -> dict[str, Any]:
    filepath = require_param(params, "file")
    loop = asyncio.get_running_loop()
    try:
        content = await loop.run_in_executor(None, read_file_sync, filepath)
        return {"returncode": 0, "stdout": content, "stderr": ""}
    except OSError as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}


def list_dir_sync(directory: str, recursive: bool, depth: int) -> str:
    max_depth = 3
    max_entries = 500
    entries: list[str] = []
    count = 0
    for entry in sorted(os.scandir(directory), key=lambda item: item.name):
        if count >= max_entries:
            entries.append("... (truncated)")
            break
        prefix = "  " * depth
        if entry.is_dir():
            entries.append(f"{prefix}[DIR] {entry.name}/")
            if recursive and depth < max_depth:
                entries.append(list_dir_sync(entry.path, True, depth + 1))
        else:
            entries.append(f"{prefix}{entry.name}  ({entry.stat().st_size} bytes)")
        count += 1
    return "\n".join(entries)


async def list_directory(params: dict[str, Any]) -> dict[str, Any]:
    directory = require_param(params, "directory")
    recursive = params.get("recursive", False) is True
    loop = asyncio.get_running_loop()
    try:
        listing = await loop.run_in_executor(None, list_dir_sync, directory, recursive, 0)
        return {"returncode": 0, "stdout": listing, "stderr": ""}
    except OSError as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}


def write_file_sync(filepath: str, content: str) -> None:
    parent = os.path.dirname(filepath)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as handle:
        handle.write(content)


async def file_write(params: dict[str, Any]) -> dict[str, Any]:
    filepath = require_param(params, "file")
    content = params.get("content", "")
    if not isinstance(content, str):
        return {"returncode": 1, "stdout": "", "stderr": "content must be a string."}
    if len(content.encode("utf-8")) > 1_048_576:
        return {"returncode": 1, "stdout": "", "stderr": "Content exceeds 1 MB limit."}
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, write_file_sync, filepath, content)
    except OSError as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}
    return {"returncode": 0, "stdout": f"Wrote {len(content)} bytes to {filepath}.", "stderr": ""}


async def create_directory(params: dict[str, Any]) -> dict[str, Any]:
    directory = require_param(params, "directory")
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, os.makedirs, directory, 0o755, True)
        return {"returncode": 0, "stdout": f"Created {directory}", "stderr": ""}
    except OSError as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}


async def delete_directory(params: dict[str, Any]) -> dict[str, Any]:
    directory = require_param(params, "directory")
    loop = asyncio.get_running_loop()
    try:
        exists = await loop.run_in_executor(None, os.path.isdir, directory)
        if not exists:
            return {"returncode": 0, "stdout": f"Directory '{directory}' does not exist.", "stderr": ""}
        await loop.run_in_executor(None, shutil.rmtree, directory)
        return {"returncode": 0, "stdout": f"Deleted '{directory}'.", "stderr": ""}
    except OSError as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}


async def zip_project(params: dict[str, Any]) -> dict[str, Any]:
    working_dir = require_param(params, "working_dir")
    if not os.path.isdir(working_dir):
        return {"returncode": 1, "stdout": "", "stderr": f"Not a directory: {working_dir}"}

    exclude_dirs = {"node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build", ".next"}
    max_zip_size = 10 * 1024 * 1024

    buffer = io.BytesIO()
    file_count = 0
    try:
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for root, dirs, files in os.walk(working_dir):
                dirs[:] = [directory for directory in dirs if directory not in exclude_dirs]
                for filename in files:
                    file_path = os.path.join(root, filename)
                    arcname = os.path.relpath(file_path, working_dir)
                    try:
                        archive.write(file_path, arcname)
                        file_count += 1
                    except (PermissionError, OSError):
                        continue
                    if buffer.tell() > max_zip_size:
                        return {
                            "returncode": 1,
                            "stdout": "",
                            "stderr": f"Zip exceeds {max_zip_size // (1024 * 1024)} MB limit.",
                        }
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": f"Zip error: {exc}"}

    zip_bytes = buffer.getvalue()
    encoded = base64.b64encode(zip_bytes).decode("ascii")
    return {
        "returncode": 0,
        "stdout": encoded,
        "stderr": f"Zipped {file_count} files ({len(zip_bytes)} bytes)",
    }
