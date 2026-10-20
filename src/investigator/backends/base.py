"""Ports: the interfaces tools use to read telemetry, and their record types.

Real adapters (Loki, Prometheus, ArgoCD, Backstage, an incident tracker, ...) implement
the same protocols. Tools never know which implementation they are talking to.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from investigator.domain.common import LogLevel, UtcDatetime
from investigator.domain.incident import ServiceInfo

TimeRange = tuple[UtcDatetime, UtcDatetime]


class LogRecord(BaseModel):
    log_id: str
    ts: UtcDatetime
    service: str
    level: LogLevel
    message: str


class LogQuery(BaseModel):
    service: str
    start: UtcDatetime
    end: UtcDatetime
    min_level: LogLevel | None = None
    contains: str | None = None


class LogSearchResult(BaseModel):
    records: list[LogRecord]
    partial: bool = False
    truncated: bool = False
    missing_ranges: list[TimeRange] = Field(default_factory=list)


class MetricDescriptor(BaseModel):
    service: str
    metric: str
    unit: str
    description: str


class MetricQuery(BaseModel):
    service: str
    metric: str
    start: UtcDatetime
    end: UtcDatetime


class MetricSeries(BaseModel):
    service: str
    metric: str
    unit: str
    step_seconds: int
    points: list[tuple[UtcDatetime, float]]
    partial: bool = False
    missing_ranges: list[TimeRange] = Field(default_factory=list)


class ChangeQuery(BaseModel):
    start: UtcDatetime
    end: UtcDatetime
    service: str | None = None


class Deployment(BaseModel):
    deployment_id: str
    service: str
    version: str
    previous_version: str
    started_at: UtcDatetime
    finished_at: UtcDatetime
    status: Literal["succeeded", "failed", "rolled_back"]
    author: str
    summary: str


class ConfigChange(BaseModel):
    change_id: str
    service: str
    key: str
    old_value: str
    new_value: str
    changed_at: UtcDatetime
    author: str
    source: Literal["runtime_config", "feature_flag", "infra"]
    note: str


class DeploymentList(BaseModel):
    items: list[Deployment]
    partial: bool = False
    truncated: bool = False
    stale_as_of: UtcDatetime | None = None


class ConfigChangeList(BaseModel):
    items: list[ConfigChange]
    partial: bool = False
    truncated: bool = False
    stale_as_of: UtcDatetime | None = None


class PastIncident(BaseModel):
    incident_id: str
    date: str
    title: str
    services: list[str]
    symptoms: str
    root_cause: str
    resolution: str


class ScoredIncident(BaseModel):
    incident: PastIncident
    score: float


class LogStore(Protocol):
    async def search_logs(self, query: LogQuery) -> LogSearchResult: ...


class MetricStore(Protocol):
    async def get_metric(self, query: MetricQuery) -> MetricSeries: ...

    async def list_metrics(self, service: str) -> list[MetricDescriptor]: ...


class ChangeStore(Protocol):
    async def list_deployments(self, query: ChangeQuery) -> DeploymentList: ...

    async def list_config_changes(self, query: ChangeQuery) -> ConfigChangeList: ...


class TopologyStore(Protocol):
    async def get_service(self, name: str) -> ServiceInfo: ...

    async def list_services(self) -> list[ServiceInfo]: ...


class IncidentArchive(Protocol):
    async def search_incidents(
        self, query: str, service: str | None, limit: int
    ) -> list[ScoredIncident]: ...


@dataclass(frozen=True)
class Backends:
    logs: LogStore
    metrics: MetricStore
    changes: ChangeStore
    topology: TopologyStore
    history: IncidentArchive
