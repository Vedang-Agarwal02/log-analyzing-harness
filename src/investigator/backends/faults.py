"""Fault injection around backend ports: timeouts, outages, rate limits, partial/truncated/stale data.

Faults are defined per scenario in environment/faults.json and applied to backend operations, so
every tool reading that backend sees the same failure, as it would in a real outage.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from investigator.backends.base import (
    Backends,
    ChangeQuery,
    ChangeStore,
    ConfigChangeList,
    DeploymentList,
    IncidentArchive,
    LogQuery,
    LogSearchResult,
    LogStore,
    MetricDescriptor,
    MetricQuery,
    MetricSeries,
    MetricStore,
    ScoredIncident,
    TopologyStore,
)
from investigator.callctx import CURRENT_CALLER
from investigator.domain.common import parse_iso
from investigator.domain.incident import ServiceInfo
from investigator.errors import OperationTimeout, RateLimited, ServiceUnavailable

FaultType = Literal["timeout", "unavailable", "rate_limited", "partial", "truncated", "stale"]

_BACKEND_NAMES = {
    "search_logs": "log search",
    "get_metric": "metrics query",
    "list_metrics": "metric catalog",
    "list_deployments": "deployment history",
    "list_config_changes": "config change history",
    "get_service": "service catalog",
    "list_services": "service catalog",
    "search_incidents": "incident archive",
}


class FaultRule(BaseModel):
    operation: str
    fault: FaultType
    match: dict[str, str] = Field(default_factory=dict)
    first_n: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)


def load_fault_rules(path: Path) -> list[FaultRule]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [FaultRule.model_validate(item) for item in data.get("faults", [])]


class FaultInjector:
    def __init__(self, rules: list[FaultRule]) -> None:
        self.rules = list(rules)
        self._counts: dict[tuple[int, str], int] = {}
        self.fired: list[dict[str, Any]] = []

    def before(self, operation: str, fields: dict[str, Any]) -> list[FaultRule]:
        """Raise for error faults; return the data faults to apply to the result."""
        caller = CURRENT_CALLER.get()
        name = _BACKEND_NAMES.get(operation, operation)
        transforms: list[FaultRule] = []
        for index, rule in enumerate(self.rules):
            if rule.operation != operation:
                continue
            if any(str(fields.get(key)) != value for key, value in rule.match.items()):
                continue
            key = (index, caller)
            count = self._counts.get(key, 0) + 1
            self._counts[key] = count
            if rule.first_n is not None and count > rule.first_n:
                continue
            self.fired.append({"operation": operation, "fault": rule.fault, "caller": caller})
            if rule.fault == "timeout":
                raise OperationTimeout(
                    f"The {name} backend timed out", hint="Retry later or narrow the query."
                )
            if rule.fault == "unavailable":
                raise ServiceUnavailable(
                    f"The {name} backend is unavailable (HTTP 503)",
                    hint="This data source is down; use other evidence and record a data gap.",
                )
            if rule.fault == "rate_limited":
                raise RateLimited(
                    f"The {name} backend rate limited the request (HTTP 429)",
                    retry_after_s=float(rule.params.get("retry_after_s", 0.2)),
                )
            transforms.append(rule)
        return transforms

    def apply(
        self,
        rules: list[FaultRule],
        result: Any,
        *,
        window: tuple[datetime, datetime] | None = None,
    ) -> Any:
        for rule in rules:
            result = _transform(rule, result, window)
        return result


def _transform(rule: FaultRule, result: Any, window: tuple[datetime, datetime] | None) -> Any:
    if rule.fault == "partial":
        ranges = [
            (parse_iso(str(a)), parse_iso(str(b))) for a, b in rule.params.get("drop_ranges", [])
        ]
        if window is not None:
            ranges = [(a, b) for a, b in ranges if a < window[1] and b > window[0]]
        if not ranges:
            return result

        def inside(ts: datetime) -> bool:
            return any(a <= ts < b for a, b in ranges)

        if isinstance(result, LogSearchResult):
            kept = [record for record in result.records if not inside(record.ts)]
            return result.model_copy(
                update={
                    "records": kept,
                    "partial": True,
                    "missing_ranges": [*result.missing_ranges, *ranges],
                }
            )
        if isinstance(result, MetricSeries):
            kept_points = [point for point in result.points if not inside(point[0])]
            return result.model_copy(
                update={
                    "points": kept_points,
                    "partial": True,
                    "missing_ranges": [*result.missing_ranges, *ranges],
                }
            )
    if rule.fault == "truncated":
        limit = int(rule.params.get("max_items", 50))
        if isinstance(result, LogSearchResult) and len(result.records) > limit:
            return result.model_copy(update={"records": result.records[:limit], "truncated": True})
        if isinstance(result, DeploymentList | ConfigChangeList) and len(result.items) > limit:
            return result.model_copy(update={"items": result.items[:limit], "truncated": True})
    if rule.fault == "stale":
        cutoff = parse_iso(str(rule.params["hide_after"]))
        if isinstance(result, DeploymentList):
            fresh = [item for item in result.items if item.started_at < cutoff]
            return result.model_copy(update={"items": fresh, "stale_as_of": cutoff})
        if isinstance(result, ConfigChangeList):
            fresh_changes = [item for item in result.items if item.changed_at < cutoff]
            return result.model_copy(update={"items": fresh_changes, "stale_as_of": cutoff})
    return result


class FaultyLogStore:
    def __init__(self, inner: LogStore, injector: FaultInjector) -> None:
        self._inner = inner
        self._injector = injector

    async def search_logs(self, query: LogQuery) -> LogSearchResult:
        rules = self._injector.before("search_logs", {"service": query.service})
        result = await self._inner.search_logs(query)
        applied: LogSearchResult = self._injector.apply(
            rules, result, window=(query.start, query.end)
        )
        return applied


class FaultyMetricStore:
    def __init__(self, inner: MetricStore, injector: FaultInjector) -> None:
        self._inner = inner
        self._injector = injector

    async def get_metric(self, query: MetricQuery) -> MetricSeries:
        rules = self._injector.before(
            "get_metric", {"service": query.service, "metric": query.metric}
        )
        result = await self._inner.get_metric(query)
        applied: MetricSeries = self._injector.apply(rules, result, window=(query.start, query.end))
        return applied

    async def list_metrics(self, service: str) -> list[MetricDescriptor]:
        self._injector.before("list_metrics", {"service": service})
        return await self._inner.list_metrics(service)


class FaultyChangeStore:
    def __init__(self, inner: ChangeStore, injector: FaultInjector) -> None:
        self._inner = inner
        self._injector = injector

    async def list_deployments(self, query: ChangeQuery) -> DeploymentList:
        rules = self._injector.before("list_deployments", {"service": query.service})
        result = await self._inner.list_deployments(query)
        applied: DeploymentList = self._injector.apply(
            rules, result, window=(query.start, query.end)
        )
        return applied

    async def list_config_changes(self, query: ChangeQuery) -> ConfigChangeList:
        rules = self._injector.before("list_config_changes", {"service": query.service})
        result = await self._inner.list_config_changes(query)
        applied: ConfigChangeList = self._injector.apply(
            rules, result, window=(query.start, query.end)
        )
        return applied


class FaultyTopologyStore:
    def __init__(self, inner: TopologyStore, injector: FaultInjector) -> None:
        self._inner = inner
        self._injector = injector

    async def get_service(self, name: str) -> ServiceInfo:
        self._injector.before("get_service", {"service": name})
        return await self._inner.get_service(name)

    async def list_services(self) -> list[ServiceInfo]:
        self._injector.before("list_services", {})
        return await self._inner.list_services()


class FaultyIncidentArchive:
    def __init__(self, inner: IncidentArchive, injector: FaultInjector) -> None:
        self._inner = inner
        self._injector = injector

    async def search_incidents(
        self, query: str, service: str | None, limit: int
    ) -> list[ScoredIncident]:
        self._injector.before("search_incidents", {"service": service})
        return await self._inner.search_incidents(query, service, limit)


def wrap_with_faults(backends: Backends, injector: FaultInjector) -> Backends:
    return Backends(
        logs=FaultyLogStore(backends.logs, injector),
        metrics=FaultyMetricStore(backends.metrics, injector),
        changes=FaultyChangeStore(backends.changes, injector),
        topology=FaultyTopologyStore(backends.topology, injector),
        history=FaultyIncidentArchive(backends.history, injector),
    )
