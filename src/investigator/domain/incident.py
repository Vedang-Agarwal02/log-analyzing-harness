"""What the system receives: the incident report and the service catalog."""

from typing import Literal

from pydantic import BaseModel, Field

from investigator.domain.common import Severity, UtcDatetime

ServiceKind = Literal["service", "datastore", "infrastructure", "external"]


class IncidentReport(BaseModel):
    incident_id: str
    title: str
    severity: Severity
    reported_at: UtcDatetime
    reporter: str
    summary: str
    affected_services: list[str] = Field(default_factory=list)
    customer_impact: str = ""
    initial_hypothesis: str | None = None


class ServiceInfo(BaseModel):
    name: str
    kind: ServiceKind
    tier: str
    team: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
