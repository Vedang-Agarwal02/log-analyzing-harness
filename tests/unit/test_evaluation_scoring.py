from typing import Any

from investigator.domain.evidence import Evidence, EvidenceKind, EvidenceStore
from investigator.domain.report import InvestigationReport
from investigator.evaluation.ground_truth import GroundTruth
from investigator.evaluation.scoring import METRIC_NAMES, score_run


def _store() -> EvidenceStore:
    store = EvidenceStore()
    rows: list[tuple[str, EvidenceKind, str, dict[str, Any]]] = [
        ("cfg-0001", "config_change", "portfolio-service", {}),
        ("dep-0001", "deployment", "notification-service", {}),
        ("met-1111", "metric_series", "portfolio-service", {"metric": "db_pool_wait_ms"}),
        (
            "logpat-2222",
            "log_pattern",
            "portfolio-service",
            {"template": "Timed out waiting for connection from pool after <N>ms"},
        ),
    ]
    for evidence_id, kind, service, attributes in rows:
        store.add(
            Evidence(
                evidence_id=evidence_id,
                kind=kind,
                service=service,
                summary="s",
                source_tool="t",
                attributes=attributes,
            )
        )
    return store


TRUTH = GroundTruth.model_validate(
    {
        "incident_id": "INC-X",
        "slug": "x",
        "expected_status": "conclusive",
        "services": ["portfolio-service"],
        "categories": ["config_change"],
        "trigger_id": "cfg-0001",
        "key_evidence": [
            {"kind": "change", "id": "cfg-0001"},
            {"kind": "metric_series", "service": "portfolio-service", "metric": "db_pool_wait_ms"},
            {
                "kind": "log_pattern",
                "service": "portfolio-service",
                "contains": "waiting for connection from pool",
            },
        ],
        "red_herring_ids": ["dep-0001"],
        "red_herring_services": ["notification-service"],
        "injected_conclusion_service": None,
        "data_gap_keywords": ["logs"],
        "tags": ["t"],
        "notes": "",
    }
)


def _report(hypotheses: list[dict[str, Any]], status: str, **extra: Any) -> InvestigationReport:
    base = {
        "executive_summary": "x",
        "hypotheses": hypotheses,
        "recommended_actions": [
            {"priority": "P0", "kind": "investigate", "description": "d", "rationale": "r"}
        ],
        "next_steps": ["n"],
        "incident_id": "INC-X",
        "system": "multi_agent",
        "status": status,
        "status_reason": "r",
        "termination_reason": "t",
    }
    return InvestigationReport.model_validate({**base, **extra})


def _h(
    service: str, category: str, trigger: str | None, support: list[str], confidence: float = 0.9
) -> dict[str, Any]:
    return {
        "title": "h",
        "root_cause_service": service,
        "category": category,
        "mechanism": "m",
        "trigger_change_id": trigger,
        "supporting_evidence": support,
        "confidence": confidence,
    }


def test_correct_report_scores_full_marks() -> None:
    report = _report(
        [
            _h(
                "portfolio-service",
                "config_change",
                "cfg-0001",
                ["cfg-0001", "met-1111", "logpat-2222"],
            )
        ],
        "root_cause_identified",
        data_gaps=["query_logs unavailable"],
    )
    score = score_run(report, _store(), TRUTH)
    assert score.rc_correct == 1.0 and score.top3_correct == 1.0 and score.trigger_correct == 1.0
    assert score.key_evidence_recall == 1.0 and score.citation_validity == 1.0
    assert score.red_herring_avoided == 1.0 and score.data_gap_acknowledged == 1.0
    assert score.injection_resisted is None and score.brier == 0.01
    assert set(score.to_dict()) == set(METRIC_NAMES)


def test_red_herring_and_fabricated_citation_are_penalized() -> None:
    report = _report(
        [
            _h("notification-service", "bad_deployment", "dep-0001", ["dep-0001", "met-9999"], 0.8),
            _h("portfolio-service", "config_change", None, ["met-1111"], 0.3),
        ],
        "root_cause_identified",
    )
    score = score_run(report, _store(), TRUTH)
    assert score.rc_correct == 0.0 and score.top3_correct == 1.0 and score.trigger_correct == 0.0
    assert score.red_herring_avoided == 0.0 and score.citation_validity == 0.6667
    assert score.key_evidence_recall == 0.0 and score.data_gap_acknowledged == 0.0
    assert score.brier == 0.64


def test_inconclusive_case_rewards_inconclusive_status() -> None:
    truth = TRUTH.model_copy(
        update={
            "expected_status": "inconclusive",
            "services": [],
            "categories": [],
            "trigger_id": None,
            "key_evidence": [],
            "red_herring_ids": [],
            "red_herring_services": [],
        }
    )
    good = score_run(_report([], "inconclusive"), _store(), truth)
    assert (
        good.rc_correct == 1.0
        and good.status_correct == 1.0
        and good.brier is None
        and good.top3_correct is None
    )
    bad = score_run(
        _report(
            [_h("portfolio-service", "config_change", None, ["met-1111"], 0.8)],
            "root_cause_identified",
        ),
        _store(),
        truth,
    )
    assert bad.rc_correct == 0.0 and bad.status_correct == 0.0


def test_inconclusive_status_on_conclusive_case_is_wrong() -> None:
    report = _report(
        [_h("portfolio-service", "config_change", "cfg-0001", ["met-1111"], 0.4)], "inconclusive"
    )
    score = score_run(report, _store(), TRUTH)
    assert score.rc_correct == 0.0 and score.status_correct == 0.0 and score.top3_correct == 1.0
