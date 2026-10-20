"""Wiring: build the environment for a scenario and run one investigation (multi-agent or single-agent)."""

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from investigator.agents.catalog import AgentCatalog, load_catalog
from investigator.backends.faults import FaultInjector, load_fault_rules, wrap_with_faults
from investigator.backends.simulated import build_simulated_backends, load_services
from investigator.baseline.single_agent import SingleAgentInvestigator
from investigator.domain.incident import IncidentReport, ServiceInfo
from investigator.harness.anthropic_client import AnthropicClient
from investigator.harness.gemini_client import GeminiClient
from investigator.harness.llm import LLMClient
from investigator.harness.recording import RecordingLLM
from investigator.harness.routing import RoutingLLM, provider_for
from investigator.harness.tools import ToolRegistry
from investigator.orchestrator.coordinator import Coordinator
from investigator.orchestrator.outcome import InvestigationOutcome
from investigator.reporting.markdown import render_report
from investigator.settings import Settings
from investigator.tools.registry import build_registry

SystemChoice = Literal["multi", "single"]


def scenario_dir(settings: Settings, scenario_id: str) -> Path:
    path = settings.path(settings.datasets_dir) / scenario_id
    if not (path / "incident.json").exists():
        raise FileNotFoundError(
            f"scenario {scenario_id!r} not found under {path.parent}; run `investigator generate-data`"
        )
    return path


def list_scenarios(settings: Settings) -> list[str]:
    root = settings.path(settings.datasets_dir)
    if not root.exists():
        return []
    return sorted(path.name for path in root.iterdir() if (path / "incident.json").exists())


def load_incident(root: Path) -> IncidentReport:
    return IncidentReport.model_validate_json((root / "incident.json").read_text(encoding="utf-8"))


def build_environment(
    root: Path, settings: Settings, *, seed: int = 0
) -> tuple[ToolRegistry, list[ServiceInfo]]:
    telemetry = root / "telemetry"
    latency = (settings.simulation.latency_ms_min, settings.simulation.latency_ms_max)
    backends = build_simulated_backends(telemetry, latency_ms=latency, seed=seed)
    injector = FaultInjector(load_fault_rules(root / "environment" / "faults.json"))
    return build_registry(wrap_with_faults(backends, injector)), load_services(telemetry)


def build_catalog(settings: Settings) -> AgentCatalog:
    return load_catalog(
        settings.path(settings.agents_file),
        settings.path(settings.prompts_dir),
        default_model=settings.llm.model,
        default_max_tokens=settings.llm.max_tokens,
    )


def build_llm(
    settings: Settings, *, record: Path | None = None, replay: Path | None = None
) -> LLMClient:
    """Replay a cassette, or route each request to Claude or Gemini by its model id (optionally recording)."""
    if replay is not None:
        return RecordingLLM(replay, mode="replay")
    provider_for(settings.llm.model)  # fail fast on a model id no provider serves
    client = RoutingLLM(
        {
            "anthropic": lambda: AnthropicClient(settings.llm.anthropic_settings()),
            "gemini": lambda: GeminiClient(settings.llm.gemini_settings()),
        }
    )
    if record is not None:
        return RecordingLLM(record, mode="record", inner=client)
    return client


def new_run_id(scenario_id: str, system: str) -> str:
    return f"{scenario_id}-{system}-{datetime.now(UTC):%Y%m%dT%H%M%S}-{secrets.token_hex(2)}"


def write_artifacts(outcome: InvestigationOutcome) -> None:
    if outcome.run_dir is None:
        return
    run_dir = outcome.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(outcome.report.model_dump_json(indent=1), encoding="utf-8")
    markdown = render_report(outcome.report, outcome.incident, outcome.evidence, outcome.metrics)
    (run_dir / "report.md").write_text(markdown, encoding="utf-8")
    (run_dir / "evidence.json").write_text(
        outcome.evidence.model_dump_json(indent=1), encoding="utf-8"
    )
    (run_dir / "metrics.json").write_text(
        json.dumps(outcome.metrics.to_dict(), indent=1, sort_keys=True), encoding="utf-8"
    )


async def run_investigation(
    *,
    scenario_id: str,
    system: SystemChoice,
    settings: Settings,
    llm: LLMClient,
    run_dir: Path | None = None,
    seed: int = 0,
    run_id: str | None = None,
) -> InvestigationOutcome:
    root = scenario_dir(settings, scenario_id)
    incident = load_incident(root)
    registry, services = build_environment(root, settings, seed=seed)
    catalog = build_catalog(settings)
    identifier = run_id or new_run_id(scenario_id, system)
    if system == "multi":
        coordinator = Coordinator(
            catalog=catalog,
            llm=llm,
            registry=registry,
            services=services,
            settings=settings,
            run_id=identifier,
            run_dir=run_dir,
        )
        outcome = await coordinator.investigate(incident)
    else:
        baseline = SingleAgentInvestigator(
            catalog=catalog,
            llm=llm,
            registry=registry,
            services=services,
            settings=settings,
            run_id=identifier,
            run_dir=run_dir,
        )
        outcome = await baseline.investigate(incident)
    write_artifacts(outcome)
    return outcome
