"""Evidence records minted by tools, the per-run evidence store, and citing statements."""

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, Field

from investigator.domain.common import StatementKind, UtcDatetime

EvidenceKind = Literal[
    "log_pattern",
    "log_sample",
    "metric_series",
    "deployment",
    "config_change",
    "dependency",
    "incident_history",
    "external",
]


class Evidence(BaseModel):
    evidence_id: str
    kind: EvidenceKind
    service: str | None = None
    timestamp: UtcDatetime | None = None
    summary: str
    source_tool: str
    source_call_id: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


class EvidenceStore(BaseModel):
    """Every evidence item any tool returned during one investigation, keyed by ID."""

    items: dict[str, Evidence] = Field(default_factory=dict)

    def add(self, evidence: Evidence) -> None:
        """Insert or replace. When two tool calls return the same ID, the one with the smaller
        source_call_id wins, so parallel completion order never changes the store."""
        current = self.items.get(evidence.evidence_id)
        if current is None or evidence.source_call_id <= current.source_call_id:
            self.items[evidence.evidence_id] = evidence

    def get(self, evidence_id: str) -> Evidence | None:
        return self.items.get(evidence_id)

    def has(self, evidence_id: str) -> bool:
        return evidence_id in self.items

    def missing(self, evidence_ids: Iterable[str]) -> list[str]:
        return sorted({eid for eid in evidence_ids if eid not in self.items})

    def sorted_items(self) -> list[Evidence]:
        return [self.items[key] for key in sorted(self.items)]

    def count(self) -> int:
        return len(self.items)


class Statement(BaseModel):
    text: str = Field(description="One factual sentence.")
    kind: StatementKind = Field(
        description="observation = read directly from evidence (must cite IDs); "
        "inference = concluded from observations; assumption = believed but unverified."
    )
    evidence_ids: list[str] = Field(
        default_factory=list, description="Evidence IDs exactly as returned by tools."
    )
