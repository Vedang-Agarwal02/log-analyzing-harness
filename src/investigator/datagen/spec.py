"""Scenario specification models (the YAML scenario language) and time-offset parsing.

All times in a scenario are offsets relative to `t0`, the incident onset. Examples:
"-4m" (4 minutes before), "+20m", "-49h30m", "-3d", "0m".
"""

import re
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from investigator.domain.common import LogLevel, RootCauseCategory, Severity, UtcDatetime

_OFFSET_RE = re.compile(r"^(?P<sign>[+-])?(?:(?P<d>\d+)d)?(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?$")


def parse_offset(text: str) -> timedelta:
    """Parse offsets such as '-4m', '+2h', '-2d3h', '49h30m' or '0m' into a timedelta."""
    value = text.strip()
    match = _OFFSET_RE.match(value)
    if match is None or not any(match.group(part) for part in ("d", "h", "m")):
        raise ValueError(f"invalid offset {text!r}; use forms like '-4m', '+2h', '-2d3h' or '0m'")
    delta = timedelta(
        days=int(match.group("d") or 0),
        hours=int(match.group("h") or 0),
        minutes=int(match.group("m") or 0),
    )
    return -delta if match.group("sign") == "-" else delta


class IncidentSpec(BaseModel):
    title: str
    severity: Severity
    reporter: str
    summary: str
    affected_services: list[str]
    customer_impact: str
    initial_hypothesis: str | None = None


class WindowSpec(BaseModel):
    before: str = "6h"
    after: str = "40m"


class ChangeSpec(BaseModel):
    key: str
    type: Literal["deploy", "config"]
    service: str
    at: str
    author: str = "unknown"
    version: str | None = None
    previous_version: str | None = None
    status: Literal["succeeded", "failed", "rolled_back"] = "succeeded"
    summary: str = ""
    config_key: str | None = None
    old: str | None = None
    new: str | None = None
    source: Literal["runtime_config", "feature_flag", "infra"] = "runtime_config"
    note: str = ""


class MetricSignal(BaseModel):
    service: str
    metric: str
    shape: Literal["step", "ramp", "spike", "sawtooth", "multiply", "periodic_spike"]
    at: str
    until: str | None = None
    to: float | None = None
    start_value: float | None = None
    factor: float | None = None
    period: str | None = None
    duration: str | None = None


class LogSignal(BaseModel):
    service: str
    level: LogLevel
    template: str
    at: str
    until: str | None = None
    rate_per_min: float | None = None
    every: str | None = None
    count: int | None = None


class ClockSkew(BaseModel):
    service: str
    offset_seconds: int


class HistorySpec(BaseModel):
    key: str
    date: str
    title: str
    services: list[str]
    symptoms: str
    root_cause: str
    resolution: str


class FaultSpec(BaseModel):
    operation: Literal[
        "search_logs",
        "get_metric",
        "list_metrics",
        "list_deployments",
        "list_config_changes",
        "get_service",
        "list_services",
        "search_incidents",
    ]
    fault: Literal["timeout", "unavailable", "rate_limited", "partial", "truncated", "stale"]
    match: dict[str, str] = Field(default_factory=dict)
    first_n: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class KeyEvidenceSpec(BaseModel):
    kind: Literal["change", "metric_series", "log_pattern"]
    change: str | None = None
    service: str | None = None
    metric: str | None = None
    contains: str | None = None


class GroundTruthSpec(BaseModel):
    expected_status: Literal["conclusive", "inconclusive"]
    services: list[str] = Field(default_factory=list)
    categories: list[RootCauseCategory] = Field(default_factory=list)
    trigger: str | None = None
    key_evidence: list[KeyEvidenceSpec] = Field(default_factory=list)
    red_herring_changes: list[str] = Field(default_factory=list)
    red_herring_services: list[str] = Field(default_factory=list)
    injected_conclusion_service: str | None = None
    data_gap_keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str = ""


class ScenarioSpec(BaseModel):
    id: str
    slug: str
    seed: int
    t0: UtcDatetime
    reported_offset: str = "+10m"
    window: WindowSpec = Field(default_factory=WindowSpec)
    metrics_step_seconds: int = 60
    log_noise_scale: float = 1.0
    incident: IncidentSpec
    changes: list[ChangeSpec] = Field(default_factory=list)
    metric_signals: list[MetricSignal] = Field(default_factory=list)
    log_signals: list[LogSignal] = Field(default_factory=list)
    clock_skews: list[ClockSkew] = Field(default_factory=list)
    history: list[HistorySpec] = Field(default_factory=list)
    faults: list[FaultSpec] = Field(default_factory=list)
    ground_truth: GroundTruthSpec


def load_scenario(path: Path) -> ScenarioSpec:
    return ScenarioSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_all_scenarios(directory: Path) -> list[ScenarioSpec]:
    specs = [load_scenario(path) for path in sorted(directory.glob("*.yaml"))]
    return sorted(specs, key=lambda spec: spec.id)
