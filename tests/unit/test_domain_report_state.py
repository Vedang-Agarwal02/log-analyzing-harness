from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from investigator.domain.critique import Critique
from investigator.domain.findings import FindingDraft, FindingRecord
from investigator.domain.incident import IncidentReport, ServiceInfo
from investigator.domain.plan import Task, TaskSpec
from investigator.domain.report import (
    InvestigationReport,
    ReportDraft,
    ReportHypothesis,
    derive_status,
)
from investigator.domain.state import InvestigationState


def _rh(confidence: float) -> ReportHypothesis:
    return ReportHypothesis(
        title="t",
        root_cause_service="orders-service",
        category="bad_deployment",
        mechanism="m",
        confidence=confidence,
        supporting_evidence=["dep-0001"],
    )


@pytest.mark.parametrize(
    ("confidence", "critic_accepted", "expected"),
    [
        (0.9, True, "root_cause_identified"),
        (0.9, None, "root_cause_identified"),
        (0.9, False, "probable_root_cause"),
        (0.75, None, "root_cause_identified"),
        (0.6, True, "probable_root_cause"),
        (0.5, None, "probable_root_cause"),
        (0.49, True, "inconclusive"),
    ],
)
def test_derive_status_thresholds(
    confidence: float, critic_accepted: bool | None, expected: str
) -> None:
    status, reason = derive_status([_rh(confidence)], critic_accepted)
    assert status == expected
    assert reason


def test_derive_status_without_hypotheses_is_inconclusive() -> None:
    assert derive_status([], True)[0] == "inconclusive"


def test_report_draft_requires_an_action() -> None:
    with pytest.raises(ValidationError):
        ReportDraft.model_validate({"executive_summary": "x", "recommended_actions": []})


def test_investigation_report_extends_draft() -> None:
    draft = ReportDraft.model_validate(
        {
            "executive_summary": "x",
            "recommended_actions": [
                {"priority": "P0", "kind": "investigate", "description": "d", "rationale": "r"}
            ],
        }
    )
    report = InvestigationReport(
        **draft.model_dump(),
        incident_id="INC-X",
        system="multi_agent",
        status="inconclusive",
        status_reason="r",
        termination_reason="completed",
    )
    assert report.fallback is False
    assert report.recommended_actions[0].priority == "P0"


def _state() -> InvestigationState:
    incident = IncidentReport(
        incident_id="INC-X",
        title="t",
        severity="SEV2",
        reported_at=datetime(2026, 3, 10, 10, 20, tzinfo=UTC),
        reporter="r",
        summary="s",
    )
    services = [
        ServiceInfo(name="b-svc", kind="service", tier="core", team="t", description="d"),
        ServiceInfo(name="a-svc", kind="service", tier="core", team="t", description="d"),
    ]
    return InvestigationState(run_id="run-1", incident=incident, services=services)


def test_state_helpers_sort_and_filter() -> None:
    state = _state()
    assert state.service_names() == {"a-svc", "b-svc"}
    assert state.latest_hypotheses() is None and state.latest_critique() is None
    finding = FindingDraft.model_validate(
        {"summary": "s", "statements": [{"text": "t", "kind": "inference"}]}
    )
    state.findings["R1-T2"] = FindingRecord(
        task_id="R1-T2", agent="dependency", round=1, finding=finding
    )
    state.findings["R1-T1"] = FindingRecord(
        task_id="R1-T1", agent="observability", round=1, finding=finding
    )
    assert [f.task_id for f in state.sorted_findings()] == ["R1-T1", "R1-T2"]
    spec = TaskSpec(task_id="T1", agent="observability", objective="o")
    state.tasks["R1-T2"] = Task(task_id="R1-T2", round=1, origin="plan", spec=spec, status="done")
    state.tasks["R1-T1"] = Task(task_id="R1-T1", round=1, origin="plan", spec=spec)
    assert [t.task_id for t in state.tasks_with_status("pending", "done")] == ["R1-T1", "R1-T2"]
    state.critiques.append(Critique(verdict="accept", summary="ok"))
    assert state.latest_critique() is not None
    clone = InvestigationState.model_validate_json(state.model_dump_json())
    assert clone.tasks["R1-T1"].status == "pending"
