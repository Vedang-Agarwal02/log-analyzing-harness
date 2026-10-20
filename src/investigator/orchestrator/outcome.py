"""The result of one investigation run (either system)."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from investigator.domain.evidence import EvidenceStore
from investigator.domain.incident import IncidentReport
from investigator.domain.report import InvestigationReport, SystemName
from investigator.domain.state import InvestigationState
from investigator.harness.tracing import RunMetrics


@dataclass
class InvestigationOutcome:
    run_id: str
    system: SystemName
    incident: IncidentReport
    report: InvestigationReport
    evidence: EvidenceStore
    metrics: RunMetrics
    events: list[dict[str, Any]] = field(default_factory=list)
    state: InvestigationState | None = None
    run_dir: Path | None = None
