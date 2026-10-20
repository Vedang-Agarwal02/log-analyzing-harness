"""Critic output: verdict, issues and requests for more evidence."""

from typing import Literal

from pydantic import BaseModel, Field

CritiqueVerdict = Literal["accept", "revise", "investigate"]
IssueType = Literal[
    "unsupported_claim",
    "ignored_contradiction",
    "correlation_not_causation",
    "timeline_inconsistency",
    "alternative_not_considered",
    "insufficient_evidence",
    "overconfidence",
    "data_gap",
]


class CritiqueIssue(BaseModel):
    hypothesis_id: str | None = None
    issue_type: IssueType
    detail: str
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceRequest(BaseModel):
    target_agent: str = Field(description="Specialist to run the follow-up, e.g. 'observability'.")
    objective: str = Field(description="Precisely what to check.")
    services: list[str] = Field(default_factory=list)
    rationale: str = Field(description="Why this evidence would change the conclusion.")


class Critique(BaseModel):
    verdict: CritiqueVerdict = Field(
        description="accept = well supported; revise = reasoning flaws, no new data needed; "
        "investigate = specific additional evidence is needed."
    )
    summary: str
    issues: list[CritiqueIssue] = Field(default_factory=list)
    evidence_requests: list[EvidenceRequest] = Field(
        default_factory=list, description="Required when verdict is 'investigate' (1-4 requests)."
    )
