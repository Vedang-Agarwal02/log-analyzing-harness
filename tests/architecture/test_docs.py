"""The architecture and dataset docs exist and stay in sync with the code and the scenarios."""

from pathlib import Path

from investigator.tools.registry import DATA_TOOL_NAMES


def test_architecture_doc_has_diagrams_and_names_every_tool_and_agent(repo_root: Path) -> None:
    text = (repo_root / "docs" / "architecture.md").read_text(encoding="utf-8")
    assert text.count("```mermaid") >= 4
    for name in DATA_TOOL_NAMES:
        assert name in text, name
    for agent in ("planner", "observability", "dependency", "reasoning", "critic", "reporter"):
        assert agent in text.lower(), agent


def test_dataset_doc_lists_every_scenario(repo_root: Path) -> None:
    text = (repo_root / "docs" / "dataset.md").read_text(encoding="utf-8")
    scenario_ids = sorted(
        path.stem for path in (repo_root / "datasets" / "scenarios").glob("*.yaml")
    )
    assert len(scenario_ids) == 12
    for scenario_id in scenario_ids:
        assert f"| {scenario_id} |" in text, scenario_id
