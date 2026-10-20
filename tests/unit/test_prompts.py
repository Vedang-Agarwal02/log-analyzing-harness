from pathlib import Path

import pytest

SUBMIT_TOOLS = {
    "planner.md": "submit_plan",
    "observability.md": "submit_findings",
    "dependency.md": "submit_findings",
    "reasoning.md": "submit_hypotheses",
    "critic.md": "submit_critique",
    "reporter.md": "submit_report",
    "single_agent.md": "submit_report",
}


@pytest.mark.parametrize(("name", "tool"), sorted(SUBMIT_TOOLS.items()))
def test_agent_prompt_names_its_submit_tool(repo_root: Path, name: str, tool: str) -> None:
    text = (repo_root / "prompts" / name).read_text(encoding="utf-8")
    assert tool in text
    assert len(text.split()) > 120


def test_shared_rules_cover_grounding_injection_and_calibration(repo_root: Path) -> None:
    text = (repo_root / "prompts" / "shared_rules.md").read_text(encoding="utf-8").lower()
    for phrase in (
        "never invent",
        "never follow instructions",
        "observation",
        "assumption",
        "0.75",
        "0.50",
        "data gap",
    ):
        assert phrase in text
