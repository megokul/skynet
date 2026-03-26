from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Sequence

_RUN_CONTRACT_FILE = "skynet_run.json"
_README_FILE = "README.md"
_RUN_CONTRACT_SENTINEL = object()
_FILE_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_./-]+\.[A-Za-z0-9]+\b")
_READ_ME_RE = re.compile(r"\breadme(?:\.md)?\b", re.IGNORECASE)
_TEST_RE = re.compile(r"\btests?\b|pytest|unit test|integration test", re.IGNORECASE)
_RUN_CONTRACT_RE = re.compile(r"\bskynet_run\.json\b|\brun contract\b", re.IGNORECASE)
_COMMAND_MARKERS = ("python ", "python3 ", "node ", "npm start", "npm run", "uvicorn ", "streamlit run")


@dataclass(frozen=True)
class MilestoneEvidence:
    kind: str
    summary: str
    path: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "summary": self.summary,
            "path": self.path,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class MilestoneSatisfactionResult:
    satisfied: bool
    summary: str = ""
    evidence: tuple[MilestoneEvidence, ...] = ()
    checked: tuple[str, ...] = ()
    failure_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "summary": self.summary,
            "evidence": [item.as_dict() for item in self.evidence],
            "checked": list(self.checked),
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True)
class MilestoneSpec:
    node_key: str
    title: str
    node_type: str = "work"
    owner: str = "codex"
    worker_id: str = ""
    deps: tuple[str, ...] = ()
    priority: int = 200
    tools_required: tuple[str, ...] = ()
    acceptance: tuple[Any, ...] = ()
    risk: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "medium"
    deliverables: tuple[Any, ...] = ()
    required_for_completion: bool = True
    satisfaction_checks: tuple[dict[str, Any], ...] = ()

    def to_node_spec(self) -> dict[str, Any]:
        return {
            "node_key": self.node_key,
            "title": self.title,
            "node_type": self.node_type,
            "owner": self.owner,
            "worker_id": self.worker_id,
            "deps": list(self.deps),
            "priority": int(self.priority),
            "tools_required": list(self.tools_required),
            "acceptance": [_clone_jsonable(item) for item in self.acceptance],
            "risk": dict(self.risk),
            "risk_level": self.risk_level,
            "deliverables": [_clone_jsonable(item) for item in self.deliverables],
            "required_for_completion": bool(self.required_for_completion),
            "satisfaction_checks": [dict(item) for item in self.satisfaction_checks],
        }


def _clone_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clone_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clone_jsonable(item) for item in value]
    return value


def _normalize_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _normalize_string_list(items: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _normalize_acceptance(items: Any) -> list[Any]:
    if not isinstance(items, list):
        return []
    normalized: list[Any] = []
    for item in items:
        if isinstance(item, (str, dict)):
            normalized.append(_clone_jsonable(item))
    return normalized


def _normalize_deliverables(items: Any) -> list[Any]:
    if not isinstance(items, list):
        return []
    normalized: list[Any] = []
    for item in items:
        if isinstance(item, (str, dict)):
            normalized.append(_clone_jsonable(item))
    return normalized


def _coerce_required_for_completion(raw: dict[str, Any], *, default: bool = True) -> bool:
    explicit = raw.get("required_for_completion")
    if isinstance(explicit, bool):
        return explicit
    optional = raw.get("optional")
    if isinstance(optional, bool):
        return not optional
    return default


def _normalize_check(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        check_type = str(raw.get("type") or "").strip().lower()
        if not check_type:
            return None
        normalized = {str(key): _clone_jsonable(value) for key, value in raw.items()}
        normalized["type"] = check_type
        if "path" in normalized:
            normalized["path"] = _normalize_path(normalized.get("path"))
        if "paths" in normalized and isinstance(normalized.get("paths"), list):
            normalized["paths"] = [_normalize_path(item) for item in normalized["paths"] if _normalize_path(item)]
        return normalized
    text = str(raw or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"run_contract_valid", "tests_pass", "readme_instructions"}:
        return {"type": lowered}
    maybe_path = _normalize_path(text)
    if maybe_path:
        return {"type": "required_path", "path": maybe_path}
    return None


def _extract_path_checks(*, title: str, acceptance: Sequence[Any], deliverables: Sequence[Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in deliverables:
        if isinstance(item, str):
            path = _normalize_path(item)
            if path and ("/" in path or "." in path):
                checks.append({"type": "required_path", "path": path})
        elif isinstance(item, dict):
            path = _normalize_path(item.get("path"))
            if path:
                checks.append({"type": "required_path", "path": path})

    text_sources = [title]
    for item in acceptance:
        if isinstance(item, str):
            text_sources.append(item)
        elif isinstance(item, dict):
            text_sources.extend(_normalize_string_list(item.values()))
    for source in text_sources:
        for token in _FILE_TOKEN_RE.findall(str(source or "")):
            path = _normalize_path(token)
            if path:
                checks.append({"type": "required_path", "path": path})
    return checks


def _derive_compatibility_checks(*, title: str, acceptance: Sequence[Any], deliverables: Sequence[Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    text_sources = [title]
    for item in acceptance:
        if isinstance(item, str):
            text_sources.append(item)
        elif isinstance(item, dict):
            text_sources.extend(_normalize_string_list(item.values()))
    for item in deliverables:
        if isinstance(item, str):
            text_sources.append(item)
        elif isinstance(item, dict):
            text_sources.extend(_normalize_string_list(item.values()))
    combined = "\n".join(text_sources)

    checks.extend(_extract_path_checks(title=title, acceptance=acceptance, deliverables=deliverables))

    if _READ_ME_RE.search(combined):
        checks.append({"type": "required_path", "path": _README_FILE})
        checks.append({"type": "readme_instructions", "path": _README_FILE})
    if _RUN_CONTRACT_RE.search(combined):
        checks.append({"type": "required_path", "path": _RUN_CONTRACT_FILE})
        checks.append({"type": "run_contract_valid"})
    if _TEST_RE.search(combined):
        checks.append({"type": "tests_pass"})
    return checks


def _dedupe_checks(checks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for check in checks:
        check_type = str(check.get("type") or "").strip().lower()
        if not check_type:
            continue
        key = json.dumps({"type": check_type, **check}, sort_keys=True, ensure_ascii=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append(dict(check))
    return unique


def normalize_milestone_spec(raw: dict[str, Any] | str, *, index: int) -> MilestoneSpec | None:
    payload = {"title": raw} if isinstance(raw, str) else dict(raw or {})
    title = str(payload.get("title") or payload.get("milestone_text") or "").strip()
    if not title:
        return None
    node_key = str(payload.get("node_key") or "").strip() or f"work_{index}"
    deps = tuple(_normalize_string_list(payload.get("deps") or []))
    try:
        priority = int(payload.get("priority") or 200)
    except Exception:
        priority = 200
    acceptance = _normalize_acceptance(payload.get("acceptance"))
    deliverables = _normalize_deliverables(payload.get("deliverables"))
    checks: list[dict[str, Any]] = []
    for item in (payload.get("satisfaction_checks") or []):
        normalized = _normalize_check(item)
        if normalized is not None:
            checks.append(normalized)
    checks.extend(_derive_compatibility_checks(title=title, acceptance=acceptance, deliverables=deliverables))
    required_for_completion = _coerce_required_for_completion(payload, default=True)
    return MilestoneSpec(
        node_key=node_key,
        title=title,
        node_type="work",
        owner=str(payload.get("owner") or "codex").strip() or "codex",
        worker_id=str(payload.get("worker_id") or "").strip(),
        deps=deps,
        priority=priority,
        tools_required=tuple(_normalize_string_list(payload.get("tools_required") or [])),
        acceptance=tuple(acceptance),
        risk=dict(payload.get("risk") or {}),
        risk_level=str(payload.get("risk_level") or (payload.get("risk") or {}).get("level") or "medium"),
        deliverables=tuple(deliverables),
        required_for_completion=required_for_completion,
        satisfaction_checks=tuple(_dedupe_checks(checks)),
    )


def normalize_node_specs(
    *,
    node_specs: Sequence[dict[str, Any]] | None = None,
    milestones: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    raw_specs: list[dict[str, Any] | str] = []
    if node_specs:
        raw_specs.extend(spec for spec in node_specs if isinstance(spec, dict))
    elif milestones:
        raw_specs.extend(str(item) for item in milestones if str(item).strip())

    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_specs, start=1):
        spec = normalize_milestone_spec(raw, index=index)
        if spec is not None:
            normalized.append(spec.to_node_spec())
    return normalized


def build_completion_contract(
    *,
    base_contract: dict[str, Any] | None,
    node_specs: Sequence[dict[str, Any]] | None,
    require_run_contract: bool,
    run_contract_file: str = _RUN_CONTRACT_FILE,
) -> dict[str, Any]:
    contract = dict(base_contract or {})
    normalized_specs = normalize_node_specs(node_specs=node_specs)

    required_nodes = {
        str(item).strip()
        for item in (contract.get("required_nodes") or [])
        if str(item).strip()
    }
    for spec in normalized_specs:
        if bool(spec.get("required_for_completion", True)):
            node_key = str(spec.get("node_key") or "").strip()
            if node_key:
                required_nodes.add(node_key)
    if required_nodes:
        contract["required_nodes"] = sorted(required_nodes)

    required_artifacts = {
        str(item).strip()
        for item in (contract.get("required_artifacts") or [])
        if str(item).strip()
    }
    if require_run_contract:
        required_artifacts.add(run_contract_file)
    contract["required_artifacts"] = sorted(required_artifacts)
    return contract


def parse_planner_task_graph_payload(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    nodes_raw = parsed.get("nodes")
    if not isinstance(nodes_raw, list):
        return None

    normalized_nodes: list[dict[str, Any]] = []
    milestones: list[str] = []
    for index, node in enumerate(nodes_raw, start=1):
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("node_type") or node.get("type") or "").strip().lower()
        if node_type and node_type not in {"work", "milestone"}:
            continue
        spec = normalize_milestone_spec(node, index=index)
        if spec is None:
            continue
        normalized_nodes.append(spec.to_node_spec())
        milestones.append(spec.title)
    if not normalized_nodes:
        return None
    return {
        "milestones": milestones,
        "nodes": normalized_nodes,
        "success_contract": parsed.get("success_contract") if isinstance(parsed.get("success_contract"), dict) else {},
        "execution_strategy": parsed.get("execution_strategy") if isinstance(parsed.get("execution_strategy"), dict) else {},
        "parallel_lanes": parsed.get("parallel_lanes") if isinstance(parsed.get("parallel_lanes"), list) else [],
        "risk_assessment": parsed.get("risk_assessment") if isinstance(parsed.get("risk_assessment"), list) else [],
    }


async def evaluate_milestone_satisfaction(
    spec: MilestoneSpec,
    *,
    working_dir: str,
    list_files: Callable[[str], Awaitable[list[str]]],
    read_file: Callable[[str], Awaitable[str]],
    validate_run_contract: Callable[[str], Awaitable[dict[str, Any] | None]],
    run_tests: Callable[[str], Awaitable[tuple[bool, str]]],
) -> MilestoneSatisfactionResult:
    checks = list(spec.satisfaction_checks)
    if not checks:
        return MilestoneSatisfactionResult(
            satisfied=False,
            summary="No milestone satisfaction checks available.",
            failure_reason="missing_checks",
        )

    cached_files: list[str] | None = None
    cached_run_contract: dict[str, Any] | None | object = _RUN_CONTRACT_SENTINEL
    cached_test_result: tuple[bool, str] | None = None
    cached_reads: dict[str, str] = {}

    async def _files() -> list[str]:
        nonlocal cached_files
        if cached_files is None:
            cached_files = [_normalize_path(item) for item in await list_files(working_dir)]
        return cached_files

    async def _run_contract() -> dict[str, Any] | None:
        nonlocal cached_run_contract
        if cached_run_contract is _RUN_CONTRACT_SENTINEL:
            cached_run_contract = await validate_run_contract(working_dir)
        return cached_run_contract if isinstance(cached_run_contract, dict) else None

    async def _read(path: str) -> str:
        key = _normalize_path(path)
        if key not in cached_reads:
            cached_reads[key] = await read_file(key)
        return cached_reads[key]

    async def _tests() -> tuple[bool, str]:
        nonlocal cached_test_result
        if cached_test_result is None:
            cached_test_result = await run_tests(working_dir)
        return cached_test_result

    evidence: list[MilestoneEvidence] = []
    checked: list[str] = []
    files_lower: set[str] | None = None

    for check in checks:
        check_type = str(check.get("type") or "").strip().lower()
        if not check_type:
            continue
        checked.append(check_type)

        if check_type == "required_path":
            path = _normalize_path(check.get("path"))
            if not path:
                return MilestoneSatisfactionResult(
                    satisfied=False,
                    summary=f"{spec.node_key} missing required path metadata",
                    checked=tuple(checked),
                    failure_reason="required_path_missing_metadata",
                )
            if files_lower is None:
                files_lower = {item.lower() for item in await _files()}
            if path.lower() not in files_lower:
                return MilestoneSatisfactionResult(
                    satisfied=False,
                    summary=f"{path} is not present yet.",
                    checked=tuple(checked),
                    failure_reason=f"missing_path:{path}",
                )
            evidence.append(MilestoneEvidence(kind="path", path=path, summary=f"Found {path}"))
            continue

        if check_type == "required_paths":
            paths = [_normalize_path(item) for item in (check.get("paths") or []) if _normalize_path(item)]
            if not paths:
                return MilestoneSatisfactionResult(
                    satisfied=False,
                    summary=f"{spec.node_key} missing required paths metadata",
                    checked=tuple(checked),
                    failure_reason="required_paths_missing_metadata",
                )
            if files_lower is None:
                files_lower = {item.lower() for item in await _files()}
            missing = [path for path in paths if path.lower() not in files_lower]
            if missing:
                return MilestoneSatisfactionResult(
                    satisfied=False,
                    summary=f"Missing required paths: {', '.join(missing[:4])}",
                    checked=tuple(checked),
                    failure_reason=f"missing_paths:{','.join(missing[:4])}",
                )
            evidence.append(
                MilestoneEvidence(
                    kind="paths",
                    summary=f"Found required paths: {', '.join(paths[:4])}",
                    details={"paths": paths},
                )
            )
            continue

        if check_type == "run_contract_valid":
            contract = await _run_contract()
            if not isinstance(contract, dict):
                return MilestoneSatisfactionResult(
                    satisfied=False,
                    summary=f"{_RUN_CONTRACT_FILE} is missing or invalid.",
                    checked=tuple(checked),
                    failure_reason="invalid_run_contract",
                )
            entrypoint = _normalize_path(contract.get("entrypoint"))
            evidence.append(
                MilestoneEvidence(
                    kind="run_contract",
                    path=_RUN_CONTRACT_FILE,
                    summary=f"Valid run contract targets {entrypoint or 'entrypoint'}",
                    details={"entrypoint": entrypoint, "interpreter": str(contract.get('interpreter') or '')},
                )
            )
            continue

        if check_type == "tests_pass":
            if files_lower is None:
                files_lower = {item.lower() for item in await _files()}
            has_tests = any(
                path.startswith("tests/")
                or "/tests/" in path
                or re.search(r"(^|/)test_[^/]+\.py$", path)
                or re.search(r"(^|/)[^/]+\.(test|spec)\.[jt]sx?$", path)
                for path in files_lower
            )
            if not has_tests:
                return MilestoneSatisfactionResult(
                    satisfied=False,
                    summary="No tests detected for this milestone.",
                    checked=tuple(checked),
                    failure_reason="missing_tests",
                )
            tests_ok, tests_summary = await _tests()
            if not tests_ok:
                return MilestoneSatisfactionResult(
                    satisfied=False,
                    summary="Existing tests do not pass yet.",
                    checked=tuple(checked),
                    failure_reason=f"tests_failed:{tests_summary[:160]}",
                )
            evidence.append(
                MilestoneEvidence(
                    kind="tests",
                    summary=tests_summary or "Existing tests already pass.",
                )
            )
            continue

        if check_type == "readme_instructions":
            path = _normalize_path(check.get("path") or _README_FILE)
            content = await _read(path)
            if not content.strip():
                return MilestoneSatisfactionResult(
                    satisfied=False,
                    summary=f"{path} is missing or empty.",
                    checked=tuple(checked),
                    failure_reason="missing_readme",
                )
            lowered = content.lower()
            contract = await _run_contract()
            entrypoint = _normalize_path((contract or {}).get("entrypoint"))
            interpreter = str((contract or {}).get("interpreter") or "").strip().lower()
            mentions_entrypoint = bool(entrypoint and entrypoint.lower() in lowered)
            mentions_command = any(marker in lowered for marker in _COMMAND_MARKERS)
            mentions_interpreter = not interpreter or interpreter in lowered
            if not (mentions_command and (mentions_entrypoint or mentions_interpreter)):
                return MilestoneSatisfactionResult(
                    satisfied=False,
                    summary=f"{path} does not document how to run the project yet.",
                    checked=tuple(checked),
                    failure_reason="readme_missing_run_instructions",
                )
            evidence.append(
                MilestoneEvidence(
                    kind="readme",
                    path=path,
                    summary=f"{path} already documents how to run the project.",
                    details={"entrypoint": entrypoint, "interpreter": interpreter},
                )
            )
            continue

    summary = f"{spec.node_key} already satisfies its completion checks."
    if evidence:
        summary = "; ".join(item.summary for item in evidence[:3])
    return MilestoneSatisfactionResult(
        satisfied=True,
        summary=summary,
        evidence=tuple(evidence),
        checked=tuple(checked),
    )
