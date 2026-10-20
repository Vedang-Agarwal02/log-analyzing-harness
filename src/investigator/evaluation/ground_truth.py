"""Ground truth for a scenario. Only the evaluator reads ground_truth.json."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class KeyEvidence(BaseModel):
    kind: Literal["change", "metric_series", "log_pattern"]
    id: str | None = None
    service: str | None = None
    metric: str | None = None
    contains: str | None = None


class GroundTruth(BaseModel):
    incident_id: str
    slug: str
    expected_status: Literal["conclusive", "inconclusive"]
    services: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    trigger_id: str | None = None
    key_evidence: list[KeyEvidence] = Field(default_factory=list)
    red_herring_ids: list[str] = Field(default_factory=list)
    red_herring_services: list[str] = Field(default_factory=list)
    injected_conclusion_service: str | None = None
    data_gap_keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str = ""


def load_ground_truth(path: Path) -> GroundTruth:
    return GroundTruth.model_validate_json(path.read_text(encoding="utf-8"))
