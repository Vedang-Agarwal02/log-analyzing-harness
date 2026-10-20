"""Metric tools: query_metrics, list_metrics and detect_anomalies."""

import asyncio
from datetime import datetime, timedelta
from statistics import fmean
from typing import Any

from pydantic import BaseModel, Field

from investigator.backends.analysis import (
    ChangePoint,
    detect_change_point,
    downsample,
    series_id,
    summarize,
)
from investigator.backends.base import Backends, MetricQuery, MetricSeries
from investigator.domain.common import UtcDatetime, iso
from investigator.domain.evidence import Evidence
from investigator.errors import OperationError
from investigator.harness.tools import Tool, ToolContext, ToolOutput, tool
from investigator.tools.common import validate_window

MAX_WINDOW = timedelta(days=4)
Point = tuple[datetime, float]


class QueryMetricsInput(BaseModel):
    service: str = Field(description="Service name exactly as in the service catalog.")
    metric: str = Field(description="Metric name from list_metrics, e.g. 'latency_p99_ms'.")
    start: UtcDatetime = Field(description="Window start, ISO-8601 UTC.")
    end: UtcDatetime = Field(description="Window end, ISO-8601 UTC (exclusive).")
    step_seconds: int = Field(
        default=60, ge=60, le=3600, description="Bucket size; results are capped at 60 points."
    )


class ListMetricsInput(BaseModel):
    service: str = Field(description="Service name exactly as in the service catalog.")


class DetectAnomaliesInput(BaseModel):
    start: UtcDatetime = Field(description="Window start, ISO-8601 UTC.")
    end: UtcDatetime = Field(description="Window end, ISO-8601 UTC (exclusive).")
    services: list[str] | None = Field(
        default=None, description="Services to scan; omit to scan every service."
    )
    max_results: int = Field(default=15, ge=1, le=30)


def _bucket(points: list[Point], start: datetime, step_seconds: int) -> list[Point]:
    buckets: dict[int, list[float]] = {}
    order: list[int] = []
    for ts, value in points:
        key = int((ts - start).total_seconds() // step_seconds)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(value)
    return [
        (start + timedelta(seconds=key * step_seconds), round(fmean(buckets[key]), 3))
        for key in order
    ]


def _change_dict(change: ChangePoint | None) -> dict[str, Any] | None:
    if change is None:
        return None
    return {
        "at": iso(change.at),
        "before_mean": change.before_mean,
        "after_mean": change.after_mean,
        "severity": change.severity,
        "direction": change.direction,
    }


def metric_evidence(
    series: MetricSeries,
    start: datetime,
    end: datetime,
    change: ChangePoint | None,
    stats: dict[str, float],
) -> Evidence:
    sid = series_id(series.service, series.metric, iso(start), iso(end))
    if change is not None:
        text = (
            f"{series.service} {series.metric}: {change.before_mean} -> {change.after_mean} {series.unit} "
            f"from {iso(change.at)} (max {stats.get('max')})"
        )
    elif stats:
        text = (
            f"{series.service} {series.metric}: no significant change; mean {stats['mean']}, max {stats['max']} "
            f"{series.unit} ({iso(start)} to {iso(end)})"
        )
    else:
        text = f"{series.service} {series.metric}: no data between {iso(start)} and {iso(end)}"
    return Evidence(
        evidence_id=sid,
        kind="metric_series",
        service=series.service,
        timestamp=change.at if change is not None else start,
        summary=text,
        source_tool="query_metrics",
        attributes={
            "metric": series.metric,
            "unit": series.unit,
            "window": [iso(start), iso(end)],
            "summary": stats,
            "change_point": _change_dict(change),
            "partial": series.partial,
        },
    )


def build_metric_tools(backends: Backends) -> list[Tool]:
    @tool(
        name="query_metrics",
        input_model=QueryMetricsInput,
        description=(
            "Fetch one metric time series for a service (at most 60 points) with summary statistics and the "
            "most significant change point. Call it to confirm an anomaly, measure its size and onset, and "
            "compare against the baseline before the incident."
        ),
    )
    async def query_metrics(args: QueryMetricsInput, ctx: ToolContext) -> ToolOutput:
        validate_window(args.start, args.end, max_window=MAX_WINDOW)
        query = MetricQuery(
            service=args.service, metric=args.metric, start=args.start, end=args.end
        )
        series = await backends.metrics.get_metric(query)
        stats = summarize(series.points)
        change = detect_change_point(series.points)
        step = max(args.step_seconds, series.step_seconds)
        shown = downsample(_bucket(series.points, args.start, step))
        evidence = metric_evidence(series, args.start, args.end, change, stats)
        warnings: list[str] = []
        if series.partial:
            warnings.append("Partial data: some time ranges are missing (see missing_ranges).")
        if not series.points:
            warnings.append("No data points in this window.")
        data = {
            "series_id": evidence.evidence_id,
            "service": series.service,
            "metric": series.metric,
            "unit": series.unit,
            "window": {"start": iso(args.start), "end": iso(args.end)},
            "step_seconds": step,
            "points": [[iso(ts), value] for ts, value in shown],
            "summary": stats,
            "change_point": _change_dict(change),
            "missing_ranges": [[iso(a), iso(b)] for a, b in series.missing_ranges],
        }
        return ToolOutput(data=data, evidence=[evidence], partial=series.partial, warnings=warnings)

    @tool(
        name="list_metrics",
        input_model=ListMetricsInput,
        description=(
            "List the metrics available for a service (name, unit, description). Call it before query_metrics "
            "when you are unsure of a metric's exact name."
        ),
    )
    async def list_metrics(args: ListMetricsInput, ctx: ToolContext) -> ToolOutput:
        descriptors = await backends.metrics.list_metrics(args.service)
        warnings = (
            []
            if descriptors
            else [
                "This component exposes no metrics; external providers are visible only through their callers' metrics."
            ]
        )
        metrics = [
            {"metric": d.metric, "unit": d.unit, "description": d.description} for d in descriptors
        ]
        return ToolOutput(data={"service": args.service, "metrics": metrics}, warnings=warnings)

    @tool(
        name="detect_anomalies",
        input_model=DetectAnomaliesInput,
        description=(
            "Scan the metrics of many services in a time window and return the most significant change points, "
            "ranked by severity. Call it first to see which services and metrics changed and when, then confirm "
            "with query_metrics and query_logs."
        ),
    )
    async def detect_anomalies(args: DetectAnomaliesInput, ctx: ToolContext) -> ToolOutput:
        validate_window(args.start, args.end, max_window=MAX_WINDOW)
        if args.services:
            names = sorted(set(args.services))
        else:
            names = [
                s.name for s in await backends.topology.list_services() if s.kind != "external"
            ]
        errors: list[str] = []
        targets: list[tuple[str, str]] = []
        for name in names:
            try:
                descriptors = await backends.metrics.list_metrics(name)
            except OperationError as exc:
                errors.append(f"{name}: {exc.error_type} ({exc})")
                continue
            targets.extend((name, descriptor.metric) for descriptor in descriptors)

        async def fetch(service: str, metric: str) -> MetricSeries | str:
            try:
                return await backends.metrics.get_metric(
                    MetricQuery(service=service, metric=metric, start=args.start, end=args.end)
                )
            except OperationError as exc:
                return f"{service}/{metric}: {exc.error_type}"

        results = await asyncio.gather(*(fetch(service, metric) for service, metric in targets))
        partial = bool(errors)
        found: list[tuple[ChangePoint, MetricSeries]] = []
        for result in results:
            if isinstance(result, str):
                errors.append(result)
                partial = True
                continue
            partial = partial or result.partial
            change = detect_change_point(result.points)
            if change is not None:
                found.append((change, result))
        found.sort(key=lambda item: (-item[0].severity, item[1].service, item[1].metric))
        top = found[: args.max_results]
        evidence: list[Evidence] = []
        rows: list[dict[str, Any]] = []
        for change, series in top:
            item = metric_evidence(series, args.start, args.end, change, summarize(series.points))
            item = item.model_copy(update={"source_tool": "detect_anomalies"})
            evidence.append(item)
            rows.append(
                {
                    "series_id": item.evidence_id,
                    "service": series.service,
                    "metric": series.metric,
                    "unit": series.unit,
                    "change_point": iso(change.at),
                    "before_mean": change.before_mean,
                    "after_mean": change.after_mean,
                    "direction": change.direction,
                    "severity": change.severity,
                }
            )
        warnings: list[str] = []
        if errors:
            warnings.append(f"{len(errors)} series could not be read; see errors.")
        if len(found) > len(top):
            warnings.append(f"Showing the {len(top)} most severe of {len(found)} change points.")
        data = {
            "window": {"start": iso(args.start), "end": iso(args.end)},
            "scanned_series": len(targets),
            "anomalies": rows,
            "errors": errors[:10],
        }
        return ToolOutput(data=data, evidence=evidence, partial=partial, warnings=warnings)

    return [detect_anomalies, list_metrics, query_metrics]
