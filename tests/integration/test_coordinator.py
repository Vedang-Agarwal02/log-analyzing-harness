import json
from pathlib import Path

from investigator.settings import Settings
from tests.fakes import build_coordinator, load_incident, standard_llm

AGENTS = {"planner", "observability", "dependency", "reasoning", "critic", "reporter"}


async def test_coordinator_runs_the_full_pipeline(test_settings: Settings, tmp_path: Path) -> None:
    llm = standard_llm()
    coordinator = build_coordinator(test_settings, "INC-01", llm, run_dir=tmp_path)
    outcome = await coordinator.investigate(load_incident(test_settings, "INC-01"))
    assert {call.agent_name for call in llm.calls} == AGENTS
    assert outcome.state is not None
    assert sorted(outcome.state.tasks) == ["R1-T1", "R1-T2"]
    assert all(task.status == "done" for task in outcome.state.tasks.values())
    assert sorted(outcome.state.findings) == ["R1-T1", "R1-T2"]
    report = outcome.report
    assert report.system == "multi_agent" and report.termination_reason == "critic_accepted"
    assert report.status == "root_cause_identified" and report.fallback is False
    assert all(outcome.evidence.has(e) for h in report.hypotheses for e in h.supporting_evidence)
    events = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    types = [event["type"] for event in events]
    assert types[0] == "run_start" and types[-1] == "run_end"
    assert {
        "task_added",
        "task_start",
        "task_end",
        "llm_call",
        "tool_call",
        "hypotheses",
        "critique",
    } <= set(types)
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["phase"] == "done"
    assert outcome.metrics.llm_calls == len(llm.calls)
