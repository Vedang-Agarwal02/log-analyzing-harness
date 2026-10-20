"""Orchestration behaviors required by the assignment, proven with scripted agents (no API calls)."""

from typing import Any

from investigator.harness.llm import LLMRequest
from investigator.harness.scripted import ScriptedLLM, sequence, text_response
from investigator.orchestrator.outcome import InvestigationOutcome
from investigator.settings import Settings
from investigator.tools.registry import DATA_TOOL_NAMES
from tests.fakes import (
    build_coordinator,
    critic,
    load_incident,
    observability,
    planner,
    reasoning,
    standard_llm,
)


def _with(settings: Settings, **sections: dict[str, Any]) -> Settings:
    data = settings.model_dump()
    for key, values in sections.items():
        data[key] = {**data[key], **values}
    return Settings.model_validate(data)


async def _run(
    settings: Settings, llm: ScriptedLLM, scenario: str = "INC-01"
) -> InvestigationOutcome:
    coordinator = build_coordinator(settings, scenario, llm)
    return await coordinator.investigate(load_incident(settings, scenario))


def _first_call(llm: ScriptedLLM, agent: str) -> LLMRequest:
    return llm.calls_for(agent)[0]


async def test_specialists_run_in_parallel(test_settings: Settings) -> None:
    llm = standard_llm(delay_s=0.05)
    await _run(test_settings, llm)
    assert llm.max_in_flight >= 2


async def test_dependent_task_waits_and_receives_upstream_findings(test_settings: Settings) -> None:
    llm = standard_llm(planner=planner(dependent=True))
    outcome = await _run(test_settings, llm)
    assert outcome.state is not None
    assert outcome.state.tasks["R1-T2"].spec.depends_on == ["R1-T1"]
    order = [call.agent_name for call in llm.calls]
    last_observability = max(i for i, name in enumerate(order) if name == "observability")
    first_dependency = order.index("dependency")
    assert first_dependency > last_observability
    from tests.fakes import first_user_text

    context = first_user_text(_first_call(llm, "dependency"))
    assert "## Results of tasks this task depends on" in context and "### R1-T1" in context


async def test_critic_request_triggers_a_followup_round(test_settings: Settings) -> None:
    llm = standard_llm(critic=critic(("investigate", "accept")))
    outcome = await _run(test_settings, llm)
    state = outcome.state
    assert state is not None
    assert state.tasks["R2-T1"].status == "done" and state.tasks["R2-T1"].origin == "followup"
    assert len(state.hypothesis_rounds) == 2 and len(state.critiques) == 2
    assert outcome.report.termination_reason == "critic_accepted"
    assert outcome.report.status == "root_cause_identified"


async def test_max_rounds_terminates_and_caps_status(test_settings: Settings) -> None:
    llm = standard_llm(
        critic=critic(("investigate",)), reasoning=reasoning(confidence=0.6, increment=0.1)
    )
    outcome = await _run(test_settings, llm)
    state = outcome.state
    assert state is not None and state.round == 3
    assert sorted(state.tasks) == ["R1-T1", "R1-T2", "R2-T1", "R3-T1"]
    assert outcome.report.termination_reason == "max_rounds_reached"
    assert outcome.report.status == "probable_root_cause"


async def test_stagnation_stops_the_loop(test_settings: Settings) -> None:
    llm = standard_llm(critic=critic(("investigate",)))
    outcome = await _run(test_settings, llm)
    assert outcome.report.termination_reason == "stagnated"
    assert outcome.state is not None and outcome.state.round == 2


async def test_revise_verdict_reasons_again_without_new_tasks(test_settings: Settings) -> None:
    llm = standard_llm(critic=critic(("revise", "accept")))
    outcome = await _run(test_settings, llm)
    state = outcome.state
    assert state is not None
    assert sorted(state.tasks) == ["R1-T1", "R1-T2"]
    assert (
        len(state.hypothesis_rounds) == 2 and outcome.report.termination_reason == "critic_accepted"
    )


async def test_failed_specialist_becomes_a_data_gap(test_settings: Settings) -> None:
    llm = standard_llm(observability=sequence(text_response("I will just describe it.")))
    outcome = await _run(test_settings, llm)
    state = outcome.state
    assert state is not None
    task = state.tasks["R1-T1"]
    assert task.status == "failed" and task.attempts == 2
    assert any(gap.source == "observability task R1-T1" for gap in state.data_gaps)
    assert any(failure.component == "agent:observability" for failure in state.failures)
    assert outcome.report.fallback is False


async def test_budget_exhaustion_still_produces_a_report(test_settings: Settings) -> None:
    settings = _with(test_settings, budget={"max_total_tokens": 6000, "reserve_tokens": 3000})
    outcome = await _run(settings, standard_llm())
    assert outcome.report.termination_reason == "budget_exhausted"
    assert outcome.report.fallback is False
    assert outcome.state is not None
    assert all(task.status == "failed" for task in outcome.state.tasks.values())


async def test_planner_failure_uses_the_fallback_plan(test_settings: Settings) -> None:
    llm = standard_llm(planner=sequence(text_response("no plan from me")))
    outcome = await _run(test_settings, llm)
    state = outcome.state
    assert state is not None
    assert {task.origin for task in state.tasks.values()} == {"fallback"}
    assert [state.tasks[t].spec.agent for t in sorted(state.tasks)] == [
        "observability",
        "dependency",
    ]
    assert any(failure.component == "planner" for failure in state.failures)


async def test_reporter_failure_uses_the_fallback_report(test_settings: Settings) -> None:
    llm = standard_llm(reporter=sequence(text_response("no report")))
    outcome = await _run(test_settings, llm)
    report = outcome.report
    assert report.fallback is True and report.executive_summary.startswith(
        "Automated fallback report"
    )
    assert report.hypotheses and report.status == "root_cause_identified"


async def test_tool_failure_is_visible_to_the_agent_and_recorded(test_settings: Settings) -> None:
    llm = standard_llm(observability=observability(service="risk-service"))
    coordinator = build_coordinator(test_settings, "INC-07", llm)
    outcome = await coordinator.investigate(load_incident(test_settings, "INC-07"))
    failed_logs = [
        e
        for e in outcome.events
        if e["type"] == "tool_call" and e["tool"] == "query_logs" and not e["ok"]
    ]
    assert (
        failed_logs
        and failed_logs[0]["error_type"] == "unavailable"
        and failed_logs[0]["attempts"] == 3
    )
    assert outcome.state is not None
    assert outcome.state.findings["R1-T1"].finding.data_gaps


async def test_baseline_gets_every_data_tool_and_specialists_get_subsets(
    test_settings: Settings,
) -> None:
    llm = standard_llm()
    await _run(test_settings, llm)
    data_tools = set(DATA_TOOL_NAMES)
    for agent in ("observability", "dependency"):
        offered = {spec.name for spec in _first_call(llm, agent).tools} - {"submit_findings"}
        assert offered < data_tools
    from investigator.runtime import run_investigation

    single = standard_llm()
    await run_investigation(
        scenario_id="INC-01", system="single", settings=test_settings, llm=single
    )
    offered = {spec.name for spec in _first_call(single, "single_agent").tools} - {"submit_report"}
    assert offered == data_tools


async def test_trace_metrics_match_calls(test_settings: Settings) -> None:
    llm = standard_llm()
    outcome = await _run(test_settings, llm)
    tool_events = [e for e in outcome.events if e["type"] == "tool_call"]
    assert outcome.metrics.llm_calls == len(llm.calls)
    assert outcome.metrics.tool_calls == len(tool_events)
    assert outcome.metrics.usage.total_tokens == 1200 * len(llm.calls)
