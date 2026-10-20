from datetime import UTC, datetime

from investigator.agents.context import (
    all_data_gaps,
    critic_context,
    evidence_index_block,
    planner_context,
    reasoning_context,
    reporter_context,
    single_agent_context,
    specialist_context,
)
from investigator.domain.critique import Critique
from investigator.domain.evidence import Evidence, EvidenceStore
from investigator.domain.findings import DataGap, FindingDraft, FindingRecord
from investigator.domain.hypotheses import HypothesisSet
from investigator.domain.incident import IncidentReport, ServiceInfo
from investigator.domain.plan import Task, TaskSpec
from investigator.domain.state import InvestigationState

DESCRIPTIONS = {"observability": "logs and metrics", "dependency": "changes"}


def _state() -> InvestigationState:
    incident = IncidentReport(
        incident_id="INC-X",
        title="Orders failing",
        severity="SEV2",
        reported_at=datetime(2026, 3, 10, 10, 21, tzinfo=UTC),
        reporter="r",
        summary="Errors since 10:12",
        affected_services=["orders-service"],
    )
    services = [
        ServiceInfo(
            name="orders-service",
            kind="service",
            tier="core",
            team="t",
            description="d",
            depends_on=["orders-db"],
        ),
        ServiceInfo(name="orders-db", kind="datastore", tier="data", team="t", description="d"),
    ]
    state = InvestigationState(run_id="r1", incident=incident, services=services, round=1)
    for evidence_id in ("met-bbbb", "dep-0001", "met-aaaa"):
        state.evidence.add(
            Evidence(
                evidence_id=evidence_id,
                kind="metric_series",
                service="orders-service",
                summary=f"s {evidence_id}",
                source_tool="t",
            )
        )
    finding = FindingDraft.model_validate(
        {
            "summary": "Errors rose.",
            "statements": [
                {"text": "error rate up", "kind": "observation", "evidence_ids": ["met-aaaa"]}
            ],
            "data_gaps": [{"source": "query_logs", "description": "logs down for orders-service"}],
        }
    )
    state.findings["R1-T2"] = FindingRecord(
        task_id="R1-T2", agent="dependency", round=1, finding=finding
    )
    state.findings["R1-T1"] = FindingRecord(
        task_id="R1-T1", agent="observability", round=1, finding=finding
    )
    state.data_gaps.append(DataGap(source="query_logs", description="logs down for orders-service"))
    spec = TaskSpec(task_id="T1", agent="observability", objective="look")
    state.tasks["R1-T1"] = Task(task_id="R1-T1", round=1, origin="plan", spec=spec, status="done")
    return state


def _hypotheses() -> HypothesisSet:
    return HypothesisSet.model_validate(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "title": "Bad deploy",
                    "root_cause_service": "orders-service",
                    "category": "bad_deployment",
                    "mechanism": "m",
                    "supporting_evidence": ["met-aaaa"],
                    "confidence": 0.7,
                }
            ],
            "ranking_rationale": "r",
        }
    )


def test_findings_and_evidence_are_sorted_and_gaps_deduplicated() -> None:
    state = _state()
    text = reasoning_context(state, max_rounds=3)
    assert text.index("### R1-T1") < text.index("### R1-T2")
    index = evidence_index_block(state.evidence)
    assert index.index("dep-0001") < index.index("met-aaaa") < index.index("met-bbbb")
    assert len(all_data_gaps(state)) == 1
    assert "Round 1 of 3" in text


def test_evidence_index_truncates() -> None:
    store = EvidenceStore()
    for i in range(5):
        store.add(
            Evidence(evidence_id=f"met-{i}", kind="metric_series", summary="s", source_tool="t")
        )
    assert "... 2 more items" in evidence_index_block(store, limit=3)


def test_planner_context_initial_and_followup() -> None:
    state = _state()
    initial = planner_context(
        state, DESCRIPTIONS, followup=False, max_tasks=6, max_rounds=3, lookback_hours=3
    )
    assert (
        "Create the initial plan with up to 6 tasks" in initial
        and "- observability: logs and metrics" in initial
    )
    assert "2026-03-10T07:21:00Z" in initial
    state.hypothesis_rounds.append(_hypotheses())
    state.critiques.append(
        Critique(
            verdict="investigate",
            summary="need latency",
            evidence_requests=[
                {"target_agent": "observability", "objective": "latency", "rationale": "r"}
            ],
        )
    )  # type: ignore[list-item]
    followup = planner_context(
        state, DESCRIPTIONS, followup=True, max_tasks=4, max_rounds=3, lookback_hours=3
    )
    assert "## Tasks already run" in followup and "R1-T1 | done" in followup
    assert "Critic review to act on" in followup and "at most 4 NEW tasks" in followup


def test_specialist_context_includes_dependencies_and_errors() -> None:
    state = _state()
    spec = TaskSpec(
        task_id="T2",
        agent="dependency",
        objective="correlate",
        services=["orders-service"],
        depends_on=["R1-T1"],
    )
    task = Task(task_id="R1-T2", round=1, origin="plan", spec=spec)
    text = specialist_context(
        state, task, max_tool_calls=14, lookback_hours=3, previous_error="no_output"
    )
    assert "## Results of tasks this task depends on" in text and "### R1-T1" in text
    assert "## Your previous attempt failed\nno_output" in text
    assert "at most 14 tool calls" in text


def test_critic_reporter_and_single_agent_contexts() -> None:
    state = _state()
    hypotheses = _hypotheses()
    critic = critic_context(state, hypotheses, ["H1: thin support"], max_rounds=3)
    assert "## Automated checks\n- H1: thin support" in critic and "2 round(s) left" in critic
    state.hypothesis_rounds.append(hypotheses)
    state.termination_reason = "critic_accepted"
    report = reporter_context(state, critic_accepted=True)
    assert "Termination reason: critic_accepted" in report and "root_cause_identified" in report
    assert "Critic accepted the conclusion: yes" in report
    single = single_agent_context(
        state.incident, state.services, max_tool_calls=60, lookback_hours=3
    )
    assert "at most 60 tool calls" in single and "submit_report" in single
    assert reasoning_context(state, max_rounds=3) == reasoning_context(state, max_rounds=3)
