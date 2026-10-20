"""The final report schema and the deterministic status rule."""

from typing import Literal

from pydantic import BaseModel, Field

from investigator.domain.common import RootCauseCategory, UtcDatetime
from investigator.domain.evidence import Statement

ReportStatus = Literal["root_cause_identified", "probable_root_cause", "inconclusive"]
SystemName = Literal["multi_agent", "single_agent"]
ActionPriority = Literal["P0", "P1", "P2"]
ActionKind = Literal["mitigate", "fix", "prevent", "investigate"]

HIGH_CONFIDENCE = 0.75
MEDIUM_CONFIDENCE = 0.5


class ReportHypothesis(BaseModel):
    title: str
    root_cause_service: str = Field(description="Exact service catalog name.")
    category: RootCauseCategory
    mechanism: str
    trigger_change_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence: list[str] = Field(min_length=1)
    contradicting_evidence: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class RecommendedAction(BaseModel):
    priority: ActionPriority = Field(description="P0 = mitigate now, P1 = fix, P2 = prevent.")
    kind: ActionKind
    description: str
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)


class TimelineEvent(BaseModel):
    at: UtcDatetime
    description: str
    evidence_ids: list[str] = Field(default_factory=list)


class ReportDraft(BaseModel):
    executive_summary: str = Field(description="3-6 sentences for an on-call engineer.")
    hypotheses: list[ReportHypothesis] = Field(
        default_factory=list,
        max_length=5,
        description="Ranked, most likely first; confidence non-increasing.",
    )
    facts: list[Statement] = Field(
        default_factory=list, description="Verified observations only, each with evidence IDs."
    )
    assumptions: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(min_length=1)
    next_steps: list[str] = Field(
        default_factory=list, description="Required when the top confidence is below 0.50."
    )


class InvestigationReport(ReportDraft):
    incident_id: str
    system: SystemName
    status: ReportStatus
    status_reason: str
    termination_reason: str
    fallback: bool = False


def derive_status(
    hypotheses: list[ReportHypothesis], critic_accepted: bool | None
) -> tuple[ReportStatus, str]:
    """Map the top confidence (and the critic's verdict, if a critic ran) to a report status."""
    if not hypotheses:
        return "inconclusive", "no hypotheses were produced"
    top = hypotheses[0].confidence
    if top >= HIGH_CONFIDENCE:
        if critic_accepted is False:
            return (
                "probable_root_cause",
                f"top confidence {top:.2f} is high but the critic did not accept the conclusion",
            )
        return "root_cause_identified", f"top confidence {top:.2f} >= {HIGH_CONFIDENCE}"
    if top >= MEDIUM_CONFIDENCE:
        return (
            "probable_root_cause",
            f"top confidence {top:.2f} is between {MEDIUM_CONFIDENCE} and {HIGH_CONFIDENCE}",
        )
    return "inconclusive", f"top confidence {top:.2f} < {MEDIUM_CONFIDENCE}"
