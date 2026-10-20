from datetime import UTC, datetime, timedelta
from typing import Any

from investigator.domain.critique import Critique
from investigator.domain.evidence import Evidence, EvidenceKind
from investigator.domain.findings import DataGap, FindingDraft, FindingRecord
from investigator.domain.hypotheses import HypothesisSet
from investigator.domain.incident import IncidentReport, ServiceInfo
from investigator.domain.state import InvestigationState
from investigator.harness.budget import Budget, BudgetLimits
from investigator.harness.llm import Usage
from investigator.orchestrator.policies import (
    fallback_plan,
    fallback_report,
    run_checks,
    stagnated,
    task_signature,
    termination_decision,
)

T0 = datetime(2026, 3, 10, 10, 12, tzinfo=UTC)


def _state() -> InvestigationState:
    incident = IncidentReport(
        incident_id="INC-X",
        title="t",
        severity="SEV2",
        reported_at=T0 + timedelta(minutes=9),
        reporter="r",
        summary="s",
        affected_services=["orders-service", "ghost"],
    )
    services = [
        ServiceInfo(name=n, kind="service", tier="core", team="t", description="d")
        for n in ("orders-service", "risk-service")
    ]
    state = InvestigationState(run_id="r", incident=incident, services=services, round=1)
    entries: list[tuple[str, EvidenceKind, datetime]] = [
        ("dep-0001", "deployment", T0 - timedelta(minutes=4)),
        ("dep-0002", "deployment", T0 + timedelta(minutes=20)),
        ("met-aaaa", "metric_series", T0),
        ("met-bbbb", "metric_series", T0),
    ]
    for evidence_id, kind, when in entries:
        state.evidence.add(
            Evidence(
                evidence_id=evidence_id,
                kind=kind,
                service="orders-service",
                timestamp=when,
                summary="s",
                source_tool="t",
            )
        )
    finding = FindingDraft.model_validate(
        {
            "summary": "s",
            "anomaly_onset": T0,
            "statements": [{"text": "x", "kind": "observation", "evidence_ids": ["met-aaaa"]}],
        }
    )
    state.findings["R1-T1"] = FindingRecord(
        task_id="R1-T1", agent="observability", round=1, finding=finding
    )
    return state


def _set(*items: dict[str, Any]) -> HypothesisSet:
    return HypothesisSet.model_validate({"hypotheses": list(items), "ranking_rationale": "r"})


def _h(hid: str, confidence: float, **extra: Any) -> dict[str, Any]:
    base = {
        "hypothesis_id": hid,
        "title": "t",
        "root_cause_service": "orders-service",
        "category": "bad_deployment",
        "mechanism": "m",
        "supporting_evidence": ["met-aaaa", "met-bbbb"],
        "confidence": confidence,
    }
    return {**base, **extra}


def test_run_checks_flags_timeline_support_contradictions_duplicates_and_gaps() -> None:
    state = _state()
    state.data_gaps.append(
        DataGap(source="query_logs", description="logs unavailable for orders-service")
    )
    checks = run_checks(
        state,
        _set(
            _h("H1", 0.8, trigger_change_id="dep-0002", contradicting_evidence=["met-bbbb"]),
            _h("H2", 0.6, supporting_evidence=["met-aaaa"], trigger_change_id="dep-0002"),
        ),
    )
    text = " | ".join(checks)
    assert "H1: trigger dep-0002" in text and "after the earliest observed anomaly onset" in text
    assert "H2: confidence 0.60 rests on fewer than 2" in text
    assert "H1: high confidence despite 1 contradicting" in text
    assert "H2 duplicates H1" in text
    assert "data is missing" in text
    assert run_checks(_state(), _set(_h("H1", 0.8, trigger_change_id="dep-0001"))) == []


def test_task_signature_normalizes() -> None:
    assert task_signature("observability", ["b", "a"], "  Check   Latency ") == (
        "observability",
        ("a", "b"),
        "check latency",
    )


def test_termination_decision_order_and_stagnation() -> None:
    state = _state()
    budget = Budget(BudgetLimits())
    accept = Critique(verdict="accept", summary="ok")
    investigate = Critique(
        verdict="investigate",
        summary="more",
        evidence_requests=[{"target_agent": "observability", "objective": "o", "rationale": "r"}],
    )  # type: ignore[list-item]
    assert (
        termination_decision(
            state, accept, max_rounds=3, budget=budget, stagnation_delta=0.05
        ).reason
        == "critic_accepted"
    )
    assert (
        termination_decision(
            state, investigate, max_rounds=3, budget=budget, stagnation_delta=0.05
        ).stop
        is False
    )
    state.round = 3
    assert (
        termination_decision(
            state, investigate, max_rounds=3, budget=budget, stagnation_delta=0.05
        ).reason
        == "max_rounds_reached"
    )
    state.round = 2
    exhausted = Budget(BudgetLimits(max_total_tokens=10, reserve_tokens=0))
    exhausted.record_llm_call(Usage(input_tokens=20))
    assert (
        termination_decision(
            state, investigate, max_rounds=3, budget=exhausted, stagnation_delta=0.05
        ).reason
        == "budget_exhausted"
    )
    state.hypothesis_rounds = [_set(_h("H1", 0.6)), _set(_h("H1", 0.62))]
    assert stagnated(state, 0.05)
    assert (
        termination_decision(
            state, investigate, max_rounds=3, budget=budget, stagnation_delta=0.05
        ).reason
        == "stagnated"
    )
    state.hypothesis_rounds = [_set(_h("H1", 0.6)), _set(_h("H1", 0.8))]
    assert not stagnated(state, 0.05)


def test_fallback_plan_and_report() -> None:
    state = _state()
    plan = fallback_plan(state, {"observability", "dependency"}, (T0 - timedelta(hours=3), T0))
    assert [t.agent for t in plan.tasks] == ["observability", "dependency"]
    assert plan.tasks[0].services == ["orders-service"]
    empty = fallback_report(state, reason="reporter failed")
    assert (
        empty.hypotheses == [] and empty.next_steps and "No hypotheses" in empty.executive_summary
    )
    assert [f.evidence_ids for f in empty.facts] == [["met-aaaa"]]
    state.hypothesis_rounds.append(_set(_h("H1", 0.7)))
    with_hypothesis = fallback_report(state, reason="reporter failed")
    assert with_hypothesis.hypotheses[0].confidence == 0.7
    assert with_hypothesis.recommended_actions[0].kind == "investigate"
