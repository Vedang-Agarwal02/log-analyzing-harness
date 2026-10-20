import pytest
from pydantic import ValidationError

from investigator.domain.critique import Critique
from investigator.domain.findings import FindingDraft
from investigator.domain.hypotheses import Hypothesis, HypothesisSet
from investigator.domain.plan import PlanDraft, Task, TaskSpec


def _hypothesis(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "hypothesis_id": "H1",
        "title": "Bad deploy",
        "root_cause_service": "orders-service",
        "category": "bad_deployment",
        "mechanism": "deploy -> NPE -> 500s",
        "supporting_evidence": ["dep-0001"],
        "confidence": 0.8,
    }
    base.update(overrides)
    return base


def test_finding_requires_at_least_one_statement() -> None:
    with pytest.raises(ValidationError):
        FindingDraft.model_validate({"summary": "x", "statements": []})
    finding = FindingDraft.model_validate(
        {
            "summary": "x",
            "statements": [{"text": "t", "kind": "observation", "evidence_ids": ["a"]}],
        }
    )
    assert finding.ruled_out == [] and finding.leads == [] and finding.data_gaps == []
    assert finding.anomaly_onset is None


def test_hypothesis_validates_category_confidence_and_support() -> None:
    assert Hypothesis.model_validate(_hypothesis()).trigger_change_id is None
    with pytest.raises(ValidationError):
        Hypothesis.model_validate(_hypothesis(category="cosmic_rays"))
    with pytest.raises(ValidationError):
        Hypothesis.model_validate(_hypothesis(confidence=1.5))
    with pytest.raises(ValidationError):
        Hypothesis.model_validate(_hypothesis(supporting_evidence=[]))


def test_hypothesis_set_allows_one_to_five() -> None:
    HypothesisSet.model_validate({"hypotheses": [_hypothesis()], "ranking_rationale": "r"})
    with pytest.raises(ValidationError):
        HypothesisSet.model_validate({"hypotheses": [], "ranking_rationale": "r"})
    six = [_hypothesis(hypothesis_id=f"H{i}") for i in range(6)]
    with pytest.raises(ValidationError):
        HypothesisSet.model_validate({"hypotheses": six, "ranking_rationale": "r"})


def test_critique_verdict_is_restricted() -> None:
    assert Critique.model_validate({"verdict": "accept", "summary": "ok"}).issues == []
    with pytest.raises(ValidationError):
        Critique.model_validate({"verdict": "maybe", "summary": "?"})


def test_plan_and_task_defaults() -> None:
    plan = PlanDraft.model_validate(
        {
            "action": "investigate",
            "rationale": "r",
            "tasks": [{"task_id": "T1", "agent": "observability", "objective": "look"}],
        }
    )
    spec = plan.tasks[0]
    assert spec.depends_on == [] and spec.services == [] and spec.window_start is None
    task = Task(task_id="R1-T1", round=1, origin="plan", spec=spec)
    assert task.status == "pending" and task.attempts == 0 and task.error is None
    with pytest.raises(ValidationError):
        PlanDraft.model_validate({"action": "panic", "rationale": "r"})
    assert isinstance(spec, TaskSpec)
