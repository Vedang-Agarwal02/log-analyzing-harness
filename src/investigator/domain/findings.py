"""Specialist output: findings for one task."""

from pydantic import BaseModel, Field

from investigator.domain.common import UtcDatetime
from investigator.domain.evidence import Statement


class Lead(BaseModel):
    target_agent: str = Field(description="Specialist who should follow up, e.g. 'dependency'.")
    question: str = Field(description="What exactly should be checked.")
    services: list[str] = Field(default_factory=list)


class DataGap(BaseModel):
    source: str = Field(
        description="Tool or component that could not provide data, e.g. 'query_logs'."
    )
    description: str = Field(description="What is missing and what it prevents us from concluding.")


class FindingDraft(BaseModel):
    summary: str = Field(description="2-4 sentence factual summary of what you found.")
    statements: list[Statement] = Field(
        min_length=1, description="Key observations (with evidence IDs) and inferences."
    )
    anomaly_onset: UtcDatetime | None = Field(
        default=None, description="Earliest time the incident's anomaly is visible in the evidence."
    )
    affected_services: list[str] = Field(default_factory=list)
    suspicious_change_ids: list[str] = Field(
        default_factory=list,
        description="Evidence IDs of deployments/config changes that remain plausible triggers.",
    )
    ruled_out: list[Statement] = Field(
        default_factory=list,
        description="Things you checked and found NOT related to the incident, with evidence.",
    )
    leads: list[Lead] = Field(default_factory=list)
    data_gaps: list[DataGap] = Field(default_factory=list)


class FindingRecord(BaseModel):
    task_id: str
    agent: str
    round: int
    finding: FindingDraft
