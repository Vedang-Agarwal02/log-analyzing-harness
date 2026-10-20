from typing import Any

import pytest

from investigator.agents.outputs import OUTPUT_SPECS, ValidationContext, make_validator
from investigator.domain.critique import Critique
from investigator.domain.evidence import Evidence, EvidenceKind, EvidenceStore
from investigator.domain.findings import FindingDraft
from investigator.domain.hypotheses import HypothesisSet
from investigator.domain.plan import PlanDraft
from investigator.domain.report import ReportDraft


def _store() -> EvidenceStore:
    store = EvidenceStore()
    items: list[tuple[str, EvidenceKind]] = [
        ("dep-0001", "deployment"),
        ("met-aaaa", "metric_series"),
        ("logpat-1111", "log_pattern"),
    ]
    for evidence_id, kind in items:
        store.add(
            Evidence(
                evidence_id=evidence_id,
                kind=kind,
                service="orders-service",
                summary="s",
                source_tool="t",
            )
        )
    return store


@pytest.fixture
def ctx() -> ValidationContext:
    return ValidationContext(
        evidence=_store(),
        services={"orders-service", "api-gateway"},
        specialists={"observability", "dependency"},
    )


def _task(task_id: str, agent: str = "observability", **extra: Any) -> dict[str, Any]:
    return {"task_id": task_id, "agent": agent, "objective": f"objective {task_id}", **extra}


def test_output_specs_names() -> None:
    assert {k: v.tool_name for k, v in OUTPUT_SPECS.items()} == {
        "plan": "submit_plan",
        "finding": "submit_findings",
        "hypotheses": "submit_hypotheses",
        "critique": "submit_critique",
        "report": "submit_report",
    }


def test_plan_validation(ctx: ValidationContext) -> None:
    ctx.initial_plan = True
    validate = make_validator("plan", ctx)
    good = PlanDraft.model_validate(
        {
            "action": "investigate",
            "rationale": "r",
            "tasks": [_task("T1"), _task("T2", "dependency", depends_on=["T1"])],
        }
    )
    assert validate(good) == []
    bad = PlanDraft.model_validate(
        {
            "action": "investigate",
            "rationale": "r",
            "tasks": [
                _task("T1", "wizard"),
                _task("T2", services=["ghost"], depends_on=["T9"]),
                _task("T3", depends_on=["T4"]),
                _task("T4", depends_on=["T3"]),
                _task("T5", window_start="2026-01-01T10:00:00Z", window_end="2026-01-01T09:00:00Z"),
            ],
        }
    )
    text = " | ".join(validate(bad))
    for fragment in (
        "unknown specialist 'wizard'",
        "unknown service 'ghost'",
        "unknown task 'T9'",
        "cycle",
        "window_start",
    ):
        assert fragment in text
    assert validate(PlanDraft(action="conclude", rationale="r")) == [
        "the initial plan must use action 'investigate'"
    ]
    ctx.initial_plan = False
    ctx.existing_task_ids = {"R1-T1"}
    ctx.max_tasks = 1
    followup = PlanDraft.model_validate(
        {
            "action": "investigate",
            "rationale": "r",
            "tasks": [_task("T1", depends_on=["R1-T1"]), _task("T2")],
        }
    )
    assert validate(followup) == ["at most 1 tasks are allowed now; you proposed 2"]


def test_finding_validation(ctx: ValidationContext) -> None:
    validate = make_validator("finding", ctx)
    finding = FindingDraft.model_validate(
        {
            "summary": "s",
            "statements": [
                {"text": "a", "kind": "observation", "evidence_ids": ["met-aaaa"]},
                {"text": "b", "kind": "observation"},
                {"text": "c", "kind": "inference", "evidence_ids": ["met-zzzz"]},
            ],
            "suspicious_change_ids": ["met-aaaa"],
            "leads": [{"target_agent": "oracle", "question": "q"}],
        }
    )
    text = " | ".join(validate(finding))
    assert "statements[1]: an observation must cite" in text
    assert (
        "met-zzzz" in text
        and "deployment or config change" in text
        and "unknown specialist 'oracle'" in text
    )


def _hypothesis(hid: str, confidence: float, **extra: Any) -> dict[str, Any]:
    base = {
        "hypothesis_id": hid,
        "title": "t",
        "root_cause_service": "orders-service",
        "category": "bad_deployment",
        "mechanism": "m",
        "supporting_evidence": ["dep-0001"],
        "confidence": confidence,
    }
    return {**base, **extra}


def test_hypotheses_validation(ctx: ValidationContext) -> None:
    validate = make_validator("hypotheses", ctx)
    good = HypothesisSet.model_validate(
        {
            "hypotheses": [
                _hypothesis("H1", 0.8, trigger_change_id="dep-0001"),
                _hypothesis("H2", 0.3),
            ],
            "ranking_rationale": "r",
        }
    )
    assert validate(good) == []
    bad = HypothesisSet.model_validate(
        {
            "hypotheses": [
                _hypothesis("H1", 0.3, root_cause_service="ghost"),
                _hypothesis("H1", 0.6, trigger_change_id="logpat-1111"),
            ],
            "ranking_rationale": "r",
        }
    )
    text = " | ".join(validate(bad))
    assert (
        "unique" in text
        and "non-increasing" in text
        and "unknown service 'ghost'" in text
        and "deployment or config change" in text
    )


def test_critique_validation(ctx: ValidationContext) -> None:
    validate = make_validator("critique", ctx)
    assert validate(Critique(verdict="accept", summary="ok")) == []
    assert (
        "needs at least one evidence request"
        in validate(Critique(verdict="investigate", summary="more"))[0]
    )


def test_report_validation(ctx: ValidationContext) -> None:
    validate = make_validator("report", ctx)
    action = {"priority": "P0", "kind": "investigate", "description": "d", "rationale": "r"}
    inconclusive = ReportDraft.model_validate(
        {"executive_summary": "x", "recommended_actions": [action]}
    )
    assert validate(inconclusive) == [
        "when the top confidence is below 0.50 the report is inconclusive and must list next_steps"
    ]
    bad_fact = ReportDraft.model_validate(
        {
            "executive_summary": "x",
            "hypotheses": [_hypothesis("H1", 0.9) | {"supporting_evidence": ["dep-0001"]}],
            "facts": [{"text": "f", "kind": "inference", "evidence_ids": ["dep-0001"]}],
            "recommended_actions": [action],
        }
    )
    assert any("facts must have kind 'observation'" in p for p in validate(bad_fact))
    assert make_validator("plan", ctx)(inconclusive) == [
        "unexpected output type ReportDraft for plan"
    ]
