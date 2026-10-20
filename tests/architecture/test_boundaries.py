"""Structural guarantees: ground truth is unreachable for agents, and layers stay separated."""

import ast
import json
from pathlib import Path

PROTECTED = ["harness", "backends", "tools", "agents", "orchestrator", "baseline", "reporting"]
FORBIDDEN = ("investigator.evaluation", "investigator.datagen")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _protected_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for package in PROTECTED:
        files.extend(sorted((repo_root / "src" / "investigator" / package).rglob("*.py")))
    files.append(repo_root / "src" / "investigator" / "runtime.py")
    return files


def test_agent_side_code_never_imports_evaluation_or_datagen(repo_root: Path) -> None:
    offenders = [
        f"{path.relative_to(repo_root)} imports {name}"
        for path in _protected_files(repo_root)
        for name in _imports(path)
        if name.startswith(FORBIDDEN)
    ]
    assert offenders == []


def test_harness_does_not_depend_on_domain_tools_or_orchestration(repo_root: Path) -> None:
    banned = (
        "investigator.tools",
        "investigator.agents",
        "investigator.orchestrator",
        "investigator.backends",
    )
    offenders = [
        f"{path.name} imports {name}"
        for path in sorted((repo_root / "src" / "investigator" / "harness").rglob("*.py"))
        for name in _imports(path)
        if name.startswith(banned)
    ]
    assert offenders == []


def test_no_ground_truth_identifiers_in_agent_code_or_prompts(
    repo_root: Path, generated_root: Path
) -> None:
    needles: set[str] = set()
    for truth_path in sorted(generated_root.glob("*/ground_truth.json")):
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        needles.add(truth["incident_id"])
        needles.add(truth["slug"])
        needles.update(k["contains"] for k in truth["key_evidence"] if k["kind"] == "log_pattern")
        needles.update(k["id"] for k in truth["key_evidence"] if k["kind"] == "change")
    files = [
        *_protected_files(repo_root),
        *sorted((repo_root / "prompts").glob("*.md")),
        repo_root / "config" / "agents.yaml",
    ]
    offenders = [
        f"{path.relative_to(repo_root)} contains {needle!r}"
        for path in files
        for needle in sorted(needles)
        if needle.lower() in path.read_text(encoding="utf-8").lower()
    ]
    assert offenders == []


def test_telemetry_contains_no_ground_truth_fields(generated_root: Path) -> None:
    for root in sorted(generated_root.iterdir()):
        text = "".join(
            path.read_text(encoding="utf-8") for path in sorted((root / "telemetry").iterdir())
        )
        text += (root / "environment" / "faults.json").read_text(encoding="utf-8")
        text += (root / "incident.json").read_text(encoding="utf-8")
        for field in (
            "ground_truth",
            "red_herring",
            "trigger_id",
            "key_evidence",
            "expected_status",
        ):
            assert field not in text, f"{root.name}: {field}"


def test_simulated_backends_never_open_ground_truth(repo_root: Path) -> None:
    for path in (repo_root / "src" / "investigator" / "backends").rglob("*.py"):
        assert "ground_truth" not in path.read_text(encoding="utf-8"), path.name
