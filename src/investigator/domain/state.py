"""The blackboard: the shared investigation state. Only the coordinator writes to it."""

from typing import Literal

from pydantic import BaseModel, Field

from investigator.domain.critique import Critique
from investigator.domain.evidence import EvidenceStore
from investigator.domain.findings import DataGap, FindingRecord
from investigator.domain.hypotheses import HypothesisSet
from investigator.domain.incident import IncidentReport, ServiceInfo
from investigator.domain.plan import Task, TaskStatus

Phase = Literal[
    "planning", "gathering", "hypothesizing", "critiquing", "replanning", "reporting", "done"
]


class FailureRecord(BaseModel):
    component: str
    task_id: str | None = None
    error: str


class InvestigationState(BaseModel):
    run_id: str
    incident: IncidentReport
    services: list[ServiceInfo]
    round: int = 0
    phase: Phase = "planning"
    tasks: dict[str, Task] = Field(default_factory=dict)
    findings: dict[str, FindingRecord] = Field(default_factory=dict)
    hypothesis_rounds: list[HypothesisSet] = Field(default_factory=list)
    critiques: list[Critique] = Field(default_factory=list)
    evidence: EvidenceStore = Field(default_factory=EvidenceStore)
    data_gaps: list[DataGap] = Field(default_factory=list)
    failures: list[FailureRecord] = Field(default_factory=list)
    termination_reason: str | None = None

    def service_names(self) -> set[str]:
        return {service.name for service in self.services}

    def latest_hypotheses(self) -> HypothesisSet | None:
        return self.hypothesis_rounds[-1] if self.hypothesis_rounds else None

    def latest_critique(self) -> Critique | None:
        return self.critiques[-1] if self.critiques else None

    def sorted_findings(self) -> list[FindingRecord]:
        return [self.findings[key] for key in sorted(self.findings)]

    def tasks_with_status(self, *statuses: TaskStatus) -> list[Task]:
        return [self.tasks[key] for key in sorted(self.tasks) if self.tasks[key].status in statuses]
