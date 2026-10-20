import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from investigator.domain.evidence import EvidenceStore
from investigator.domain.incident import IncidentReport
from investigator.domain.report import InvestigationReport
from investigator.harness.routing import RoutingLLM
from investigator.reporting.markdown import render_report
from investigator.runtime import build_llm, list_scenarios, run_investigation, scenario_dir
from investigator.settings import Settings
from tests.fakes import standard_llm


async def test_single_agent_baseline_runs(test_settings: Settings, tmp_path: Path) -> None:
    llm = standard_llm()
    outcome = await run_investigation(
        scenario_id="INC-01", system="single", settings=test_settings, llm=llm, run_dir=tmp_path
    )
    assert {call.agent_name for call in llm.calls} == {"single_agent"}
    report = outcome.report
    assert report.system == "single_agent" and report.termination_reason == "completed"
    assert report.status == "root_cause_identified"
    assert outcome.metrics.tool_calls == 2 and outcome.state is None
    for name in ("trace.jsonl", "report.json", "report.md", "evidence.json", "metrics.json"):
        assert (tmp_path / name).exists(), name
    assert json.loads((tmp_path / "report.json").read_text())["system"] == "single_agent"


async def test_multi_agent_run_writes_markdown_report(
    test_settings: Settings, tmp_path: Path
) -> None:
    outcome = await run_investigation(
        scenario_id="INC-01",
        system="multi",
        settings=test_settings,
        llm=standard_llm(),
        run_dir=tmp_path,
    )
    markdown = (tmp_path / "report.md").read_text()
    assert markdown.startswith("# Incident investigation: INC-01")
    assert "## Ranked hypotheses" in markdown and "## Evidence appendix" in markdown
    cited = outcome.report.hypotheses[0].supporting_evidence[0]
    assert f"`{cited}`" in markdown
    assert (tmp_path / "state.json").exists()


def test_render_report_without_hypotheses() -> None:
    incident = IncidentReport(
        incident_id="X",
        title="t",
        severity="SEV3",
        reported_at=datetime(2026, 1, 1, tzinfo=UTC),
        reporter="r",
        summary="s",
    )
    report = InvestigationReport.model_validate(
        {
            "executive_summary": "Nothing found.",
            "recommended_actions": [
                {
                    "priority": "P0",
                    "kind": "investigate",
                    "description": "Look | closer",
                    "rationale": "r",
                }
            ],
            "next_steps": ["Collect logs"],
            "incident_id": "X",
            "system": "multi_agent",
            "status": "inconclusive",
            "status_reason": "no hypotheses",
            "termination_reason": "reasoning_failed",
            "fallback": True,
        }
    )
    text = render_report(report, incident, EvidenceStore())
    assert "No hypotheses were produced." in text and "Automated fallback report" in text
    assert "Look \\| closer" in text and "## Next steps" in text


def test_scenario_discovery(test_settings: Settings) -> None:
    assert list_scenarios(test_settings) == [f"INC-{i:02d}" for i in range(1, 13)]
    with pytest.raises(FileNotFoundError):
        scenario_dir(test_settings, "INC-99")


def test_build_llm_routes_by_model_id(test_settings: Settings) -> None:
    for model in ("claude-opus-5-5", "gemini-4-argon"):
        settings = test_settings.model_copy(
            update={"llm": test_settings.llm.model_copy(update={"model": model})}
        )
        assert isinstance(build_llm(settings), RoutingLLM)
    with pytest.raises(ValueError, match="no provider for model 'scripted'"):
        build_llm(test_settings)
