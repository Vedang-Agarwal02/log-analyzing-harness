"""Model of the shared fictional platform described in datasets/platform.yaml."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from investigator.datagen.spec import HistorySpec
from investigator.domain.common import LogLevel
from investigator.domain.incident import ServiceKind


class MetricDef(BaseModel):
    unit: str
    description: str
    noise_pct: float = 5.0
    traffic: bool = False
    max_value: float | None = None


class ExtraMetric(MetricDef):
    baseline: float


class NoiseLog(BaseModel):
    level: LogLevel
    template: str
    rate_per_min: float


class PlatformService(BaseModel):
    name: str
    kind: ServiceKind
    tier: str
    team: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    profile: str | None = None
    baselines: dict[str, float] = Field(default_factory=dict)
    extra_metrics: dict[str, ExtraMetric] = Field(default_factory=dict)
    noise_logs: list[NoiseLog] = Field(default_factory=list)


class BackgroundConfigTemplate(BaseModel):
    config_key: str
    old: str
    new: str
    source: Literal["runtime_config", "feature_flag", "infra"]
    note: str


class BackgroundChanges(BaseModel):
    deploy_summaries: list[str]
    config_changes: list[BackgroundConfigTemplate]
    authors: list[str]
    deploys: int = 8
    configs: int = 5
    quiet_period: str = "2h"
    horizon: str = "7d"


@dataclass(frozen=True)
class ResolvedMetric:
    name: str
    definition: MetricDef
    baseline: float


class Platform(BaseModel):
    metric_profiles: dict[str, dict[str, MetricDef]]
    services: list[PlatformService]
    background_changes: BackgroundChanges
    history: list[HistorySpec]

    def service(self, name: str) -> PlatformService:
        for service in self.services:
            if service.name == name:
                return service
        raise KeyError(f"unknown service {name!r}")

    def service_names(self) -> set[str]:
        return {service.name for service in self.services}

    def metrics_for(self, name: str) -> dict[str, ResolvedMetric]:
        service = self.service(name)
        resolved: dict[str, ResolvedMetric] = {}
        if service.profile is not None:
            for metric, definition in self.metric_profiles[service.profile].items():
                resolved[metric] = ResolvedMetric(metric, definition, service.baselines[metric])
        for metric, extra in service.extra_metrics.items():
            definition = MetricDef(**extra.model_dump(exclude={"baseline"}))
            resolved[metric] = ResolvedMetric(metric, definition, extra.baseline)
        return dict(sorted(resolved.items()))

    def validate_references(self) -> None:
        names = self.service_names()
        problems: list[str] = []
        for service in self.services:
            for dependency in service.depends_on:
                if dependency not in names:
                    problems.append(f"{service.name} depends on unknown service {dependency}")
            if service.profile is not None:
                profile = self.metric_profiles.get(service.profile)
                if profile is None:
                    problems.append(f"{service.name} uses unknown profile {service.profile}")
                else:
                    for metric in sorted(set(profile) - set(service.baselines)):
                        problems.append(f"{service.name} has no baseline for {metric}")
        keys = [entry.key for entry in self.history]
        if len(keys) != len(set(keys)):
            problems.append("history keys must be unique")
        if problems:
            raise ValueError("; ".join(problems))


def load_platform(path: Path) -> Platform:
    platform = Platform.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    platform.validate_references()
    return platform
