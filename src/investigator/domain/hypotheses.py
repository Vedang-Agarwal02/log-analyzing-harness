"""Reasoning output: competing root-cause hypotheses."""

from pydantic import BaseModel, Field

from investigator.domain.common import RootCauseCategory


class DiscriminatingCheck(BaseModel):
    target_agent: str = Field(description="Specialist who could obtain the evidence.")
    description: str = Field(description="What evidence would confirm or refute the hypothesis.")


class Hypothesis(BaseModel):
    hypothesis_id: str = Field(description="Short id such as 'H1'.")
    title: str
    root_cause_service: str = Field(
        description="Service/component where the cause lives (exact catalog name)."
    )
    category: RootCauseCategory
    mechanism: str = Field(description="Causal chain from trigger to symptoms, step by step.")
    trigger_change_id: str | None = Field(
        default=None,
        description="Evidence ID of the deployment/config change that triggered it, if any.",
    )
    supporting_evidence: list[str] = Field(min_length=1, description="Evidence IDs.")
    contradicting_evidence: list[str] = Field(default_factory=list, description="Evidence IDs.")
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(
        ge=0.0, le=1.0, description="Calibrated probability this is the root cause."
    )
    checks: list[DiscriminatingCheck] = Field(default_factory=list)


class HypothesisSet(BaseModel):
    hypotheses: list[Hypothesis] = Field(
        min_length=1,
        max_length=5,
        description="Ranked, most likely first; confidence non-increasing.",
    )
    ranking_rationale: str
    open_questions: list[str] = Field(default_factory=list)
    changes_since_last_round: str | None = None
