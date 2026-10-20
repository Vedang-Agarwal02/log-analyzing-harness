"""An oracle that queries the tools for exactly the ground-truth evidence must score 100%,
and an empty report must score about 0%. This proves every scenario is solvable with the tools."""

import json
from datetime import timedelta
from typing import Any

import pytest

from investigator.domain.common import iso, parse_iso
from investigator.domain.evidence import EvidenceStore
from investigator.domain.report import InvestigationReport, RecommendedAction, ReportHypothesis
from investigator.evaluation.ground_truth import GroundTruth, load_ground_truth
from investigator.evaluation.scoring import score_run
from investigator.harness.budget import Budget, BudgetLimits
from investigator.harness.executor import ToolExecutor
from investigator.harness.hooks import HookRunner
from investigator.harness.llm import ToolCall
from investigator.harness.tools import ToolContext
from investigator.runtime import build_environment, load_incident, scenario_dir
from investigator.settings import Settings

SCENARIOS = [f"INC-{i:02d}" for i in range(1, 13)]


async def _oracle(
    settings: Settings, scenario_id: str
) -> tuple[EvidenceStore, GroundTruth, list[str]]:
    root = scenario_dir(settings, scenario_id)
    truth = load_ground_truth(root / "ground_truth.json")
    registry, _ = build_environment(root, settings)
    executor = ToolExecutor(
        registry,
        budget=Budget(BudgetLimits(max_tool_calls=1000)),
        hooks=HookRunner(),
        config=settings.tools.executor_config(),
    )
    store = EvidenceStore()
    ctx = ToolContext(caller="oracle", evidence=store)
    metrics = json.loads((root / "telemetry" / "metrics.json").read_text(encoding="utf-8"))
    start = parse_iso(metrics["start"])
    end = start + timedelta(seconds=metrics["step_seconds"] * metrics["points"])
    incident = load_incident(root)

    async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
        envelope: dict[str, Any] = {}
        for attempt in range(4):
            part = await executor.execute(
                ToolCall(f"oracle-{name}-{attempt}", name, args), ctx, agent="oracle", task_id=None
            )
            envelope = json.loads(part.content)
            if envelope["ok"]:
                break
        return envelope

    supporting: list[str] = []
    for key in truth.key_evidence:
        if key.kind == "change":
            window = {
                "start": iso(incident.reported_at - timedelta(days=7)),
                "end": iso(incident.reported_at + timedelta(hours=1)),
            }
            for tool_name in ("get_deployments", "get_config_changes"):
                envelope = await call(tool_name, window)
                if key.id in envelope.get("evidence_ids", []):
                    supporting.append(str(key.id))
        elif key.kind == "metric_series":
            envelope = await call(
                "query_metrics",
                {
                    "service": key.service,
                    "metric": key.metric,
                    "start": iso(start),
                    "end": iso(end),
                },
            )
            supporting.append(envelope["data"]["series_id"])
        else:
            envelope = await call(
                "query_logs",
                {
                    "service": key.service,
                    "start": iso(start),
                    "end": iso(end),
                    "contains": key.contains,
                },
            )
            supporting.extend(pattern["pattern_id"] for pattern in envelope["data"]["patterns"][:1])
    return store, truth, supporting


def _report(
    truth: GroundTruth, hypotheses: list[ReportHypothesis], status: str, gaps: list[str]
) -> InvestigationReport:
    return InvestigationReport.model_validate(
        {
            "executive_summary": "oracle",
            "hypotheses": [h.model_dump() for h in hypotheses],
            "data_gaps": gaps,
            "recommended_actions": [
                RecommendedAction(
                    priority="P0", kind="investigate", description="d", rationale="r"
                ).model_dump()
            ],
            "next_steps": ["n"],
            "incident_id": truth.incident_id,
            "system": "multi_agent",
            "status": status,
            "status_reason": "oracle",
            "termination_reason": "oracle",
        }
    )


@pytest.mark.parametrize("scenario_id", SCENARIOS)
async def test_oracle_scores_full_marks_and_null_scores_zero(
    test_settings: Settings, scenario_id: str
) -> None:
    store, truth, supporting = await _oracle(test_settings, scenario_id)
    gaps = [" ".join(truth.data_gap_keywords)] if truth.data_gap_keywords else []
    if truth.expected_status == "conclusive":
        hypothesis = ReportHypothesis(
            title="oracle",
            root_cause_service=truth.services[0],
            category=truth.categories[0],  # type: ignore[arg-type]
            mechanism="oracle",
            trigger_change_id=truth.trigger_id,
            confidence=0.9,
            supporting_evidence=supporting,
        )
        oracle = score_run(
            _report(truth, [hypothesis], "root_cause_identified", gaps), store, truth
        )
        assert oracle.key_evidence_recall == 1.0, supporting
        assert oracle.trigger_correct in (None, 1.0)
    else:
        oracle = score_run(_report(truth, [], "inconclusive", gaps), store, truth)
    assert oracle.rc_correct == 1.0 and oracle.citation_validity == 1.0
    assert oracle.red_herring_avoided in (None, 1.0) and oracle.data_gap_acknowledged in (None, 1.0)
    null = score_run(_report(truth, [], "inconclusive", []), store, truth)
    assert null.rc_correct == (1.0 if truth.expected_status == "inconclusive" else 0.0)
