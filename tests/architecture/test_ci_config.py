"""CI and container files exist and keep their key guarantees."""

from pathlib import Path
from typing import Any

import yaml


def _workflow(repo_root: Path, name: str) -> dict[Any, Any]:
    return yaml.safe_load((repo_root / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    # PyYAML follows YAML 1.1 and reads the bare key `on` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    return triggers


def _commands(workflow: dict[Any, Any]) -> str:
    return "\n".join(
        step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]
    )


def test_ci_runs_every_offline_check_on_push_and_pull_request(repo_root: Path) -> None:
    workflow = _workflow(repo_root, "ci.yml")
    assert {"push", "pull_request"} <= set(_triggers(workflow))
    commands = _commands(workflow)
    for expected in (
        "uv sync --frozen",
        "ruff format --check",
        "ruff check",
        "mypy",
        "generate-data",
        "git diff --exit-code",
        "pytest",
    ):
        assert expected in commands, expected
    assert "ANTHROPIC_API_KEY" not in (repo_root / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )


def test_paid_eval_workflow_runs_only_on_demand(repo_root: Path) -> None:
    workflow = _workflow(repo_root, "eval.yml")
    assert set(_triggers(workflow)) == {"workflow_dispatch"}
    text = (repo_root / ".github" / "workflows" / "eval.yml").read_text(encoding="utf-8")
    assert "secrets.ANTHROPIC_API_KEY" in text and "--min-accuracy" in text


def test_container_image_never_contains_secrets(repo_root: Path) -> None:
    dockerfile = (repo_root / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.14-slim" in dockerfile and "uv sync --frozen --no-dev" in dockerfile
    ignored = (repo_root / ".dockerignore").read_text(encoding="utf-8").split()
    assert ".env" in ignored and ".git" in ignored
