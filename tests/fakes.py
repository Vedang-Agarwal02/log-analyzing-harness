"""Scripted agent policies and wiring helpers for integration tests (no real LLM).

The policies are tuned to scenario INC-01 (orders-service, onset 2026-03-10T10:12Z). This is test
code: scenario knowledge is allowed here and nowhere in src/ or prompts/.
"""

import re
from pathlib import Path
from typing import Any

from investigator.agents.catalog import load_catalog
from investigator.backends.faults import FaultInjector, load_fault_rules, wrap_with_faults
from investigator.backends.simulated import build_simulated_backends, load_services
from investigator.domain.common import iso, parse_iso
from investigator.domain.incident import IncidentReport
from investigator.harness.llm import LLMClient, LLMRequest, LLMResponse
from investigator.harness.scripted import (
    Responder,
    ScriptedLLM,
    evidence_ids_in,
    first_user_text,
    last_tool_results,
    result_evidence_ids,
    sequence,
    tool_call_response,
)
from investigator.orchestrator.coordinator import Coordinator
from investigator.settings import Settings
from investigator.tools.registry import build_registry

T0 = parse_iso("2026-03-10T10:12:00Z")
WINDOW = {"start": "2026-03-10T08:12:00Z", "end": "2026-03-10T10:40:00Z"}
CHANGE_WINDOW = {"start": "2026-03-09T10:00:00Z", "end": "2026-03-10T10:40:00Z"}
ROUND_RE = re.compile(r"Round (\d+) of (\d+)")


def round_of(request: LLMRequest) -> int:
    match = ROUND_RE.search(first_user_text(request))
    return int(match.group(1)) if match else 1


def submit(tool: str, payload: dict[str, Any]) -> LLMResponse:
    return tool_call_response((tool, payload))


def _support(request: LLMRequest) -> list[str]:
    ids = evidence_ids_in(first_user_text(request))
    return [i for i in ids if i.startswith(("met-", "logpat-"))][:2] or ids[:1]


def planner(*, dependent: bool = False, followup_tasks: int = 1) -> Responder:
    def respond(request: LLMRequest) -> LLMResponse:
        if "## Tasks already run" in first_user_text(request):
            if followup_tasks == 0:
                return submit(
                    "submit_plan", {"action": "conclude", "rationale": "enough", "tasks": []}
                )
            number = round_of(request)
            tasks = [
                {
                    "task_id": f"T{i + 1}",
                    "agent": "observability",
                    "objective": f"Follow-up check {i + 1} after round {number}",
                    "services": ["orders-service"],
                }
                for i in range(followup_tasks)
            ]
            return submit(
                "submit_plan",
                {"action": "investigate", "rationale": "critic asked", "tasks": tasks},
            )
        second: dict[str, Any] = {
            "task_id": "T2",
            "agent": "dependency",
            "objective": "Find changes near the onset",
            "services": ["orders-service"],
        }
        if dependent:
            second["depends_on"] = ["T1"]
        first = {
            "task_id": "T1",
            "agent": "observability",
            "objective": "Find anomalies and errors",
            "services": ["orders-service"],
        }
        return submit(
            "submit_plan",
            {"action": "investigate", "rationale": "parallel first look", "tasks": [first, second]},
        )

    return respond


def finding(request: LLMRequest, *, onset: bool = False, suspicious: bool = False) -> LLMResponse:
    results = last_tool_results(request)
    ids = result_evidence_ids(request)
    if ids:
        statements = [
            {"text": "Telemetry inspected.", "kind": "observation", "evidence_ids": ids[:3]}
        ]
    else:
        statements = [
            {"text": "No data could be retrieved.", "kind": "assumption", "evidence_ids": []}
        ]
    payload: dict[str, Any] = {"summary": "Scripted finding.", "statements": statements}
    if onset:
        payload["anomaly_onset"] = iso(T0)
    if suspicious:
        payload["suspicious_change_ids"] = [i for i in ids if i.startswith("dep-")][:2]
    failed = [r for r in results if not r.get("ok")]
    if failed:
        payload["data_gaps"] = [
            {
                "source": "tools",
                "description": f"{len(failed)} tool call(s) failed: {failed[0]['error']['type']}",
            }
        ]
    return submit("submit_findings", payload)


def observability(service: str = "orders-service") -> Responder:
    return sequence(
        tool_call_response(
            ("query_metrics", {"service": service, "metric": "error_rate_pct", **WINDOW}),
            ("query_logs", {"service": service, "level": "WARN", **WINDOW}),
        ),
        lambda request: finding(request, onset=True),
    )


def dependency() -> Responder:
    return sequence(
        tool_call_response(
            ("get_deployments", CHANGE_WINDOW),
            ("get_service_dependencies", {"service": "orders-service"}),
        ),
        lambda request: finding(request, suspicious=True),
    )


def reasoning(confidence: float = 0.8, increment: float = 0.0) -> Responder:
    def respond(request: LLMRequest) -> LLMResponse:
        support = _support(request)
        top = min(0.95, confidence + increment * (round_of(request) - 1))
        hypotheses = [
            {
                "hypothesis_id": "H1",
                "title": "Bad deployment of orders-service",
                "root_cause_service": "orders-service",
                "category": "bad_deployment",
                "mechanism": "deploy -> errors",
                "supporting_evidence": support,
                "confidence": top,
            },
            {
                "hypothesis_id": "H2",
                "title": "Upstream dependency issue",
                "root_cause_service": "orders-service",
                "category": "dependency_failure",
                "mechanism": "dependency slow",
                "supporting_evidence": support[:1],
                "confidence": 0.2,
            },
        ]
        return submit(
            "submit_hypotheses", {"hypotheses": hypotheses, "ranking_rationale": "scripted"}
        )

    return respond


def critic(verdicts: tuple[str, ...] = ("accept",)) -> Responder:
    def respond(request: LLMRequest) -> LLMResponse:
        verdict = verdicts[min(round_of(request), len(verdicts)) - 1]
        payload: dict[str, Any] = {"verdict": verdict, "summary": f"scripted {verdict}"}
        if verdict == "investigate":
            payload["evidence_requests"] = [
                {
                    "target_agent": "observability",
                    "objective": "Check latency too",
                    "services": ["orders-service"],
                    "rationale": "more evidence",
                }
            ]
        return submit("submit_critique", payload)

    return respond


def reporter(confidence: float = 0.8) -> Responder:
    def respond(request: LLMRequest) -> LLMResponse:
        support = _support(request)
        hypotheses = (
            [
                {
                    "title": "Bad deployment of orders-service",
                    "root_cause_service": "orders-service",
                    "category": "bad_deployment",
                    "mechanism": "deploy -> errors",
                    "supporting_evidence": support,
                    "confidence": confidence,
                }
            ]
            if support
            else []
        )
        payload = {
            "executive_summary": "Scripted report.",
            "hypotheses": hypotheses,
            "facts": [
                {"text": "Error rate rose.", "kind": "observation", "evidence_ids": support[:1]}
            ]
            if support
            else [],
            "recommended_actions": [
                {
                    "priority": "P0",
                    "kind": "mitigate",
                    "description": "Roll back",
                    "rationale": "scripted",
                    "evidence_ids": support[:1],
                }
            ],
            "next_steps": ["Confirm the rollback fixed the errors."],
        }
        return submit("submit_report", payload)

    return respond


def single_agent() -> Responder:
    def report(request: LLMRequest) -> LLMResponse:
        support = result_evidence_ids(request)[:2]
        return submit(
            "submit_report",
            {
                "executive_summary": "Single agent report.",
                "hypotheses": [
                    {
                        "title": "Bad deployment",
                        "root_cause_service": "orders-service",
                        "category": "bad_deployment",
                        "mechanism": "m",
                        "supporting_evidence": support,
                        "confidence": 0.8,
                    }
                ],
                "recommended_actions": [
                    {
                        "priority": "P0",
                        "kind": "mitigate",
                        "description": "Roll back",
                        "rationale": "r",
                    }
                ],
            },
        )

    return sequence(
        tool_call_response(
            ("detect_anomalies", {**WINDOW, "services": ["orders-service", "api-gateway"]}),
            ("get_deployments", CHANGE_WINDOW),
        ),
        report,
    )


def standard_llm(*, delay_s: float = 0.0, **overrides: Responder) -> ScriptedLLM:
    responders: dict[str, Responder] = {
        "planner": planner(),
        "observability": observability(),
        "dependency": dependency(),
        "reasoning": reasoning(),
        "critic": critic(),
        "reporter": reporter(),
        "single_agent": single_agent(),
    }
    responders.update(overrides)
    return ScriptedLLM(responders, delay_s=delay_s)


def load_incident(settings: Settings, scenario_id: str) -> IncidentReport:
    path = settings.path(settings.datasets_dir) / scenario_id / "incident.json"
    return IncidentReport.model_validate_json(path.read_text(encoding="utf-8"))


def build_coordinator(
    settings: Settings, scenario_id: str, llm: LLMClient, run_dir: Path | None = None
) -> Coordinator:
    root = settings.path(settings.datasets_dir) / scenario_id
    backends = wrap_with_faults(
        build_simulated_backends(root / "telemetry"),
        FaultInjector(load_fault_rules(root / "environment" / "faults.json")),
    )
    catalog = load_catalog(
        settings.path(settings.agents_file),
        settings.path(settings.prompts_dir),
        default_model=settings.llm.model,
    )
    return Coordinator(
        catalog=catalog,
        llm=llm,
        registry=build_registry(backends),
        services=load_services(root / "telemetry"),
        settings=settings,
        run_id=f"test-{scenario_id}",
        run_dir=run_dir,
    )
