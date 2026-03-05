from __future__ import annotations

import ast
import hashlib
import re
from typing import Any

import aiosqlite

from db.store import (
    replace_code_index_refs,
    replace_code_index_symbols,
    upsert_code_index_file,
)

_JS_IMPORT_RE = re.compile(
    r"""(?:import\s+.+?\s+from\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\))""",
    flags=re.IGNORECASE,
)
_JS_FUNC_RE = re.compile(r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", flags=re.IGNORECASE)
_JS_CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b", flags=re.IGNORECASE)
_TS_EXPORT_RE = re.compile(
    r"\bexport\s+(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)",
    flags=re.IGNORECASE,
)


def detect_language(path: str) -> str:
    clean = (path or "").strip().lower()
    if clean.endswith(".py"):
        return "python"
    if clean.endswith(".ts") or clean.endswith(".tsx"):
        return "typescript"
    if clean.endswith(".js") or clean.endswith(".jsx"):
        return "javascript"
    return ""


def _python_symbols_and_refs(content: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    symbols: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    try:
        tree = ast.parse(content)
    except Exception:
        return symbols, refs

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(
                {
                    "symbol": node.name,
                    "symbol_kind": "class",
                    "line_no": int(getattr(node, "lineno", 0) or 0),
                    "signature": f"class {node.name}",
                }
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            symbols.append(
                {
                    "symbol": node.name,
                    "symbol_kind": kind,
                    "line_no": int(getattr(node, "lineno", 0) or 0),
                    "signature": f"def {node.name}(...):",
                }
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                refs.append({"to_module": str(alias.name or "").strip(), "ref_kind": "import"})
        elif isinstance(node, ast.ImportFrom):
            mod = str(node.module or "").strip()
            if mod:
                refs.append({"to_module": mod, "ref_kind": "import_from"})

    return symbols, refs


def _js_ts_symbols_and_refs(content: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    symbols: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    for idx, line in enumerate(content.splitlines(), start=1):
        for match in _JS_FUNC_RE.finditer(line):
            symbols.append(
                {
                    "symbol": match.group(1),
                    "symbol_kind": "function",
                    "line_no": idx,
                    "signature": line.strip()[:240],
                }
            )
        for match in _JS_CLASS_RE.finditer(line):
            symbols.append(
                {
                    "symbol": match.group(1),
                    "symbol_kind": "class",
                    "line_no": idx,
                    "signature": line.strip()[:240],
                }
            )
        for match in _TS_EXPORT_RE.finditer(line):
            symbols.append(
                {
                    "symbol": match.group(1),
                    "symbol_kind": "export",
                    "line_no": idx,
                    "signature": line.strip()[:240],
                }
            )
    for match in _JS_IMPORT_RE.finditer(content):
        target = (match.group(1) or match.group(2) or "").strip()
        if target:
            refs.append({"to_module": target, "ref_kind": "import"})
    return symbols, refs


def extract_symbols_and_refs(path: str, content: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lang = detect_language(path)
    if lang == "python":
        return _python_symbols_and_refs(content)
    if lang in {"javascript", "typescript"}:
        return _js_ts_symbols_and_refs(content)
    return [], []


def _sha1_text(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()


async def index_file(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    path: str,
    content: str,
) -> dict[str, int]:
    language = detect_language(path)
    await upsert_code_index_file(
        db,
        project_id=project_id,
        path=path,
        language=language,
        sha1=_sha1_text(content),
        size_bytes=len(content.encode("utf-8", errors="replace")),
    )
    symbols, refs = extract_symbols_and_refs(path, content)
    await replace_code_index_symbols(
        db,
        project_id=project_id,
        path=path,
        symbols=symbols,
    )
    await replace_code_index_refs(
        db,
        project_id=project_id,
        from_path=path,
        refs=refs,
    )
    return {"symbols": len(symbols), "refs": len(refs)}
