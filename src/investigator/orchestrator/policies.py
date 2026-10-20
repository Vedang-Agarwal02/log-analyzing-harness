"""Deterministic orchestration policies: pre-critic checks, deduplication, termination, fallbacks."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from investigator.agents.context import all_data_gaps
from investigator.domain.common import iso
from investigator.domain.critique import Critique
from investigator.domain.hypotheses import Hypothesis, HypothesisSet
from investigator.domain.plan import PlanDraft, TaskSpec
from investigator.domain.report import RecommendedAction, ReportDraft, ReportHypothesis
from investigator.domain.state import InvestigationState
from investigator.harness.budget import Budget

TIMELINE_TOLERANCE = timedelta(minutes=5)


def earliest_onset(state: InvestigationState) -> datetime | None:
    onsets = [
        r.finding.anomaly_onset
        for r in state.sorted_findings()
        if r.finding.anomaly_onset is not None
    ]
    return min(onsets) if onsets else None


def run_checks(state: InvestigationState, hypotheses: HypothesisSet) -> list[str]:
    """Deterministic checks the critic must address."""
    checks: list[str] = []
    onset = earliest_onset(state)
    seen: dict[tuple[str, str, str | None], str] = {}
    for item in hypotheses.hypotheses:
        key = (item.root_cause_service, item.category, item.trigger_change_id)
        if key in seen:
            checks.append(
                f"{item.hypothesis_id} duplicates {seen[key]} (same service, category and trigger)."
            )
        else:
            seen[key] = item.hypothesis_id
        if item.trigger_change_id and onset is not None:
            change = state.evidence.get(item.trigger_change_id)
            if (
                change is not None
                and change.timestamp is not None
                and change.timestamp > onset + TIMELINE_TOLERANCE
            ):
                checks.append(
                    f"{item.hypothesis_id}: trigger {item.trigger_change_id} at {iso(change.timestamp)} is more than "
                    f"5 minutes after the earliest observed anomaly onset {iso(onset)}; a cause cannot follow its "
                    "effect. Verify the onset and the clocks."
                )
        if item.confidence >= 0.5 and len(set(item.supporting_evidence)) < 2:
            checks.append(
                f"{item.hypothesis_id}: confidence {item.confidence:.2f} rests on fewer than 2 distinct supporting "
                "evidence items."
            )
        if item.confidence >= 0.75 and item.contradicting_evidence:
            checks.append(
                f"{item.hypothesis_id}: high confidence despite {len(item.contradicting_evidence)} contradicting "
                "evidence item(s); explain why they do not refute it."
            )
    top = hypotheses.hypotheses[0]
    for gap in all_data_gaps(state):
        if top.root_cause_service in gap.description or top.root_cause_service in gap.source:
            checks.append(
                f"Top hypothesis {top.hypothesis_id} concerns {top.root_cause_service}, but data is missing: "
                f"{gap.source}: {gap.description}"
            )
    return checks


def task_signature(
    agent: str, services: list[str], objective: str
) -> tuple[str, tuple[str, ...], str]:
    return agent, tuple(sorted(services)), " ".join(objective.lower().split())


@dataclass(frozen=True)
class TerminationDecision:
    stop: bool
    reason: str | None = None


def stagnated(state: InvestigationState, delta: float) -> bool:
    if len(state.hypothesis_rounds) < 2:
        return False
    previous = state.hypothesis_rounds[-2].hypotheses[0]
    current = state.hypothesis_rounds[-1].hypotheses[0]
    same = (previous.root_cause_service, previous.category, previous.trigger_change_id) == (
        current.root_cause_service,
        current.category,
        current.trigger_change_id,
    )
    return same and abs(previous.confidence - current.confidence) < delta


def termination_decision(
    state: InvestigationState,
    critique: Critique,
    *,
    max_rounds: int,
    budget: Budget,
    stagnation_delta: float,
) -> TerminationDecision:
    if critique.verdict == "accept":
        return TerminationDecision(True, "critic_accepted")
    if state.round >= max_rounds:
        return TerminationDecision(True, "max_rounds_reached")
    if budget.exhausted():
        return TerminationDecision(True, "budget_exhausted")
    if stagnated(state, stagnation_delta):
        return TerminationDecision(True, "stagnated")
    return TerminationDecision(False)


def fallback_plan(
    state: InvestigationState, specialists: set[str], window: tuple[datetime, datetime]
) -> PlanDraft:
    services = sorted(set(state.incident.affected_services) & state.service_names())
    start, end = window
    tasks: list[TaskSpec] = []
    if "observability" in specialists:
        tasks.append(
            TaskSpec(
                task_id="T1",
                agent="observability",
                objective="Find anomalies, their onset and the error patterns for the affected services and their dependencies.",
                services=services,
                window_start=start,
                window_end=end,
            )
        )
    if "dependency" in specialists:
        tasks.append(
            TaskSpec(
                task_id="T2",
                agent="dependency",
                objective="List deployments and configuration changes around the incident and map the dependencies of the affected services.",
                services=services,
                window_start=start,
                window_end=end,
            )
        )
    return PlanDraft(
        action="investigate",
        rationale="Fallback plan: the planner did not produce a valid plan.",
        tasks=tasks,
    )


def hypothesis_to_report(item: Hypothesis) -> ReportHypothesis:
    return ReportHypothesis(
        title=item.title,
        root_cause_service=item.root_cause_service,
        category=item.category,
        mechanism=item.mechanism,
        trigger_change_id=item.trigger_change_id,
        confidence=item.confidence,
        supporting_evidence=item.supporting_evidence,
        contradicting_evidence=item.contradicting_evidence,
        assumptions=item.assumptions,
    )


def fallback_report(state: InvestigationState, *, reason: str) -> ReportDraft:
    latest = state.latest_hypotheses()
    hypotheses = [hypothesis_to_report(item) for item in latest.hypotheses] if latest else []
    facts = [
        statement
        for record in state.sorted_findings()
        for statement in record.finding.statements
        if statement.kind == "observation" and statement.evidence_ids
    ][:12]
    gaps = [f"{gap.source}: {gap.description}" for gap in all_data_gaps(state)]
    if hypotheses:
        top = hypotheses[0]
        actions = [
            RecommendedAction(
                priority="P0",
                kind="investigate",
                description=f"Manually verify the leading hypothesis: {top.title}",
                rationale=f"Automated fallback report ({reason}).",
                evidence_ids=top.supporting_evidence[:5],
            )
        ]
        summary = (
            f"Automated fallback report ({reason}). Leading hypothesis: {top.title} in {top.root_cause_service} "
            f"(confidence {top.confidence:.2f})."
        )
    else:
        actions = [
            RecommendedAction(
                priority="P0",
                kind="investigate",
                description="Investigate manually; the automated investigation could not form hypotheses.",
                rationale=f"Automated fallback report ({reason}).",
            )
        ]
        summary = f"Automated fallback report ({reason}). No hypotheses were produced."
    next_steps = [
        "Review the findings and the evidence index in this report.",
        "Re-run the investigation once the failing component is fixed.",
    ]
    return ReportDraft(
        executive_summary=summary,
        hypotheses=hypotheses,
        facts=facts,
        data_gaps=gaps,
        recommended_actions=actions,
        next_steps=next_steps,
    )
