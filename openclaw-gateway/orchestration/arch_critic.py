from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_arch_rules(path: str) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {"layers": []}
    raw = file_path.read_text(encoding="utf-8").strip()
    if not raw:
        return {"layers": []}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {"layers": []}


def _normalize_module_path(module: str) -> str:
    value = (module or "").strip().lower().replace(".", "/")
    return value


def _layer_for_path(path: str, layers: list[dict[str, Any]]) -> str:
    normalized = (path or "").strip().lower().replace("\\", "/")
    for layer in layers:
        name = str(layer.get("name") or "").strip()
        prefixes = [str(p).strip().lower().replace("\\", "/") for p in (layer.get("path_prefixes") or [])]
        for prefix in prefixes:
            if prefix and (normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/")):
                return name
    return ""


def evaluate_architecture_refs(
    *,
    refs: list[dict[str, Any]],
    rules: dict[str, Any],
) -> list[dict[str, Any]]:
    layers = list(rules.get("layers") or [])
    findings: list[dict[str, Any]] = []
    for ref in refs:
        from_path = str(ref.get("from_path") or "").strip()
        to_module = str(ref.get("to_module") or "").strip()
        if not from_path or not to_module:
            continue
        from_layer = _layer_for_path(from_path, layers)
        if not from_layer:
            continue
        target_layer = _layer_for_path(_normalize_module_path(to_module), layers)
        if not target_layer:
            continue
        layer_cfg = next((layer for layer in layers if str(layer.get("name") or "") == from_layer), None)
        if not isinstance(layer_cfg, dict):
            continue
        allowed = {str(name).strip() for name in (layer_cfg.get("can_import_layers") or []) if str(name).strip()}
        if target_layer not in allowed:
            findings.append(
                {
                    "severity": "high",
                    "code": "ARCH_LAYER_VIOLATION",
                    "message": (
                        f"Layer '{from_layer}' imports '{target_layer}' via '{to_module}', "
                        "which violates architecture rules."
                    ),
                    "files": [from_path],
                    "suggested_fix": f"Remove cross-layer import or route through allowed layer(s): {', '.join(sorted(allowed))}",
                }
            )
    return findings
