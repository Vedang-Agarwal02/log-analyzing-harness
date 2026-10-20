"""Simulated backends serving a generated dataset (datasets/generated/<id>/telemetry)."""

import asyncio
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from investigator.backends.analysis import tokenize
from investigator.backends.base import (
    Backends,
    ChangeQuery,
    ConfigChange,
    ConfigChangeList,
    Deployment,
    DeploymentList,
    LogQuery,
    LogRecord,
    LogSearchResult,
    MetricDescriptor,
    MetricQuery,
    MetricSeries,
    PastIncident,
    ScoredIncident,
)
from investigator.domain.common import parse_iso
from investigator.domain.incident import ServiceInfo
from investigator.errors import InvalidInput, NotFound

LEVEL_RANK = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
_UNKNOWN_SERVICE_HINT = "Call list_services to see valid service names."


def _read(directory: Path, name: str) -> Any:
    return json.loads((directory / name).read_text(encoding="utf-8"))


def load_services(telemetry_dir: Path) -> list[ServiceInfo]:
    return [
        ServiceInfo.model_validate(item)
        for item in _read(telemetry_dir, "topology.json")["services"]
    ]


class SimulatedDataset:
    def __init__(
        self,
        *,
        services: list[ServiceInfo],
        logs: dict[str, list[LogRecord]],
        metrics_start: datetime,
        step_seconds: int,
        series: dict[str, dict[str, Any]],
        deployments: list[Deployment],
        config_changes: list[ConfigChange],
        history: list[PastIncident],
    ) -> None:
        self.services = sorted(services, key=lambda service: service.name)
        self.service_names = {service.name for service in services}
        self.logs = logs
        self.metrics_start = metrics_start
        self.step_seconds = step_seconds
        self.series = series
        self.deployments = deployments
        self.config_changes = config_changes
        self.history = history

    @classmethod
    def load(cls, telemetry_dir: Path) -> "SimulatedDataset":
        logs: dict[str, list[LogRecord]] = defaultdict(list)
        with (telemetry_dir / "logs.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    record = LogRecord.model_validate_json(line)
                    logs[record.service].append(record)
        for records in logs.values():
            records.sort(key=lambda record: (record.ts, record.log_id))
        metrics = _read(telemetry_dir, "metrics.json")
        return cls(
            services=load_services(telemetry_dir),
            logs=dict(logs),
            metrics_start=parse_iso(metrics["start"]),
            step_seconds=int(metrics["step_seconds"]),
            series=metrics["series"],
            deployments=[
                Deployment.model_validate(d)
                for d in _read(telemetry_dir, "deployments.json")["deployments"]
            ],
            config_changes=[
                ConfigChange.model_validate(c)
                for c in _read(telemetry_dir, "config_changes.json")["config_changes"]
            ],
            history=[
                PastIncident.model_validate(h)
                for h in _read(telemetry_dir, "history.json")["incidents"]
            ],
        )


class _Latency:
    def __init__(self, low_ms: int, high_ms: int, seed: int) -> None:
        self._low = low_ms
        self._high = high_ms
        self._rng = random.Random(seed)

    async def wait(self) -> None:
        if self._high <= 0:
            return
        await asyncio.sleep(self._rng.uniform(self._low, self._high) / 1000.0)


def _check_service(data: SimulatedDataset, service: str) -> None:
    if service not in data.service_names:
        raise NotFound(f"unknown service {service!r}", hint=_UNKNOWN_SERVICE_HINT)


def _check_window(start: datetime, end: datetime) -> None:
    if end <= start:
        raise InvalidInput("end must be after start")


class SimLogStore:
    def __init__(self, data: SimulatedDataset, latency: _Latency) -> None:
        self._data = data
        self._latency = latency

    async def search_logs(self, query: LogQuery) -> LogSearchResult:
        await self._latency.wait()
        _check_service(self._data, query.service)
        _check_window(query.start, query.end)
        min_rank = LEVEL_RANK[query.min_level] if query.min_level else 0
        needle = query.contains.lower() if query.contains else None
        records = [
            record
            for record in self._data.logs.get(query.service, [])
            if query.start <= record.ts < query.end
            and LEVEL_RANK[record.level] >= min_rank
            and (needle is None or needle in record.message.lower())
        ]
        return LogSearchResult(records=records)


class SimMetricStore:
    def __init__(self, data: SimulatedDataset, latency: _Latency) -> None:
        self._data = data
        self._latency = latency

    async def get_metric(self, query: MetricQuery) -> MetricSeries:
        await self._latency.wait()
        _check_service(self._data, query.service)
        series = self._data.series.get(query.service, {})
        if query.metric not in series:
            raise NotFound(
                f"no metric {query.metric!r} for {query.service}",
                hint="Call list_metrics for this service.",
            )
        _check_window(query.start, query.end)
        entry = series[query.metric]
        step = self._data.step_seconds
        origin = self._data.metrics_start
        first = max(0, math.ceil((query.start - origin).total_seconds() / step))
        last = min(len(entry["values"]), math.ceil((query.end - origin).total_seconds() / step))
        points = [
            (origin + timedelta(seconds=index * step), float(entry["values"][index]))
            for index in range(first, last)
        ]
        return MetricSeries(
            service=query.service,
            metric=query.metric,
            unit=entry["unit"],
            step_seconds=step,
            points=points,
        )

    async def list_metrics(self, service: str) -> list[MetricDescriptor]:
        await self._latency.wait()
        _check_service(self._data, service)
        series = self._data.series.get(service, {})
        return [
            MetricDescriptor(
                service=service, metric=name, unit=entry["unit"], description=entry["description"]
            )
            for name, entry in sorted(series.items())
        ]


class SimChangeStore:
    def __init__(self, data: SimulatedDataset, latency: _Latency) -> None:
        self._data = data
        self._latency = latency

    def _check(self, query: ChangeQuery) -> None:
        if query.service is not None:
            _check_service(self._data, query.service)
        _check_window(query.start, query.end)

    async def list_deployments(self, query: ChangeQuery) -> DeploymentList:
        await self._latency.wait()
        self._check(query)
        items = [
            item
            for item in self._data.deployments
            if query.start <= item.started_at < query.end
            and (query.service is None or item.service == query.service)
        ]
        return DeploymentList(
            items=sorted(items, key=lambda item: (item.started_at, item.deployment_id))
        )

    async def list_config_changes(self, query: ChangeQuery) -> ConfigChangeList:
        await self._latency.wait()
        self._check(query)
        items = [
            item
            for item in self._data.config_changes
            if query.start <= item.changed_at < query.end
            and (query.service is None or item.service == query.service)
        ]
        return ConfigChangeList(
            items=sorted(items, key=lambda item: (item.changed_at, item.change_id))
        )


class SimTopologyStore:
    def __init__(self, data: SimulatedDataset, latency: _Latency) -> None:
        self._data = data
        self._latency = latency

    async def get_service(self, name: str) -> ServiceInfo:
        await self._latency.wait()
        for service in self._data.services:
            if service.name == name:
                return service
        raise NotFound(f"unknown service {name!r}", hint=_UNKNOWN_SERVICE_HINT)

    async def list_services(self) -> list[ServiceInfo]:
        await self._latency.wait()
        return list(self._data.services)


class SimIncidentArchive:
    def __init__(self, data: SimulatedDataset, latency: _Latency) -> None:
        self._data = data
        self._latency = latency

    async def search_incidents(
        self, query: str, service: str | None, limit: int
    ) -> list[ScoredIncident]:
        await self._latency.wait()
        wanted = set(tokenize(query))
        if not wanted and service is None:
            raise InvalidInput("query must contain at least one keyword of 3 or more characters")
        documents = [
            (
                incident,
                set(
                    tokenize(
                        " ".join(
                            [
                                incident.title,
                                incident.symptoms,
                                incident.root_cause,
                                *incident.services,
                            ]
                        )
                    )
                ),
            )
            for incident in self._data.history
        ]
        total = len(documents)
        frequency = {
            token: sum(1 for _, tokens in documents if token in tokens) for token in wanted
        }
        scored: list[ScoredIncident] = []
        for incident, tokens in documents:
            score = sum(
                math.log(1 + total / (1 + frequency[token])) for token in wanted if token in tokens
            )
            if service is not None and service in incident.services:
                score += 1.0
            if score > 0:
                scored.append(ScoredIncident(incident=incident, score=round(score, 3)))
        scored.sort(key=lambda item: (-item.score, item.incident.incident_id))
        return scored[:limit]


def build_simulated_backends(
    telemetry_dir: Path, *, latency_ms: tuple[int, int] = (0, 0), seed: int = 0
) -> Backends:
    data = SimulatedDataset.load(telemetry_dir)
    latency = _Latency(latency_ms[0], latency_ms[1], seed)
    return Backends(
        logs=SimLogStore(data, latency),
        metrics=SimMetricStore(data, latency),
        changes=SimChangeStore(data, latency),
        topology=SimTopologyStore(data, latency),
        history=SimIncidentArchive(data, latency),
    )
