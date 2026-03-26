from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_root_compose_mounts_prompt_library_for_both_services() -> None:
    compose = _read("docker-compose.yml")
    assert compose.count("/app/prompts:ro") >= 2
    assert compose.count("SKYNET_PROMPT_LIBRARY_DIR=/app/prompts") >= 2


def test_gateway_component_compose_mounts_repo_prompt_library() -> None:
    compose = _read("openclaw-gateway/docker-compose.yml")
    assert "../prompts:/app/prompts:ro" in compose
    assert "SKYNET_PROMPT_LIBRARY_DIR=/app/prompts" in compose


def test_control_plane_dockerfile_copies_prompt_library() -> None:
    dockerfile = _read("docker/skynet/Dockerfile")
    assert "COPY prompts/ ./prompts/" in dockerfile


def test_root_dockerignore_reincludes_prompts() -> None:
    dockerignore = _read(".dockerignore")
    assert "!prompts/" in dockerignore
    assert "!prompts/**" in dockerignore


def test_deploy_workflow_validates_prompt_library_in_running_containers() -> None:
    workflow = _read(".github/workflows/deploy-ec2-skynet.yml")
    assert "Validate prompt library availability" in workflow
    assert workflow.count("project_specialist_opening.md") >= 2
