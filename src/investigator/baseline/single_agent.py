"""Single-agent baseline: one agent with every data tool, and the same model, budget and report rules."""

import time
from pathlib import Path

from investigator.agents.catalog import AgentCatalog
from investigator.agents.context import single_agent_context
from investigator.agents.outputs import ValidationContext, make_validator
from investigator.domain.evidence import EvidenceStore
from investigator.domain.incident import IncidentReport, ServiceInfo
from investigator.domain.report import (
    InvestigationReport,
    RecommendedAction,
    ReportDraft,
    derive_status,
)
from investigator.harness.agent import Agent
from investigator.harness.budget import Budget
from investigator.harness.executor import ToolExecutor
from investigator.harness.hooks import HookRunner
from investigator.harness.llm import LLMClient
from investigator.harness.tools import ToolContext, ToolRegistry
from investigator.harness.tracing import RunMetrics, Tracer
from investigator.orchestrator.outcome import InvestigationOutcome
from investigator.settings import Settings


class SingleAgentInvestigator:
    def __init__(
        self,
        *,
        catalog: AgentCatalog,
        llm: LLMClient,
        registry: ToolRegistry,
        services: list[ServiceInfo],
        settings: Settings,
        run_id: str,
        run_dir: Path | None = None,
    ) -> None:
        self._catalog = catalog
        self._llm = llm
        self._services = services
        self._settings = settings
        self._run_id = run_id
        self._run_dir = run_dir
        if run_dir is not None:
            run_dir.mkdir(parents=True, exist_ok=True)
        self.tracer = Tracer(run_dir / "trace.jsonl" if run_dir else None, run_id)
        self._hooks = HookRunner([self.tracer])
        self.budget = Budget(settings.budget.limits())
        self._executor = ToolExecutor(
            registry, budget=self.budget, hooks=self._hooks, config=settings.tools.executor_config()
        )

    async def investigate(self, incident: IncidentReport) -> InvestigationOutcome:
        started = time.monotonic()
        evidence = EvidenceStore()
        spec = self._catalog.single_agent
        self.tracer.emit(
            "run_start",
            system="single_agent",
            incident_id=incident.incident_id,
            model=self._settings.llm.model,
        )
        context = ValidationContext(
            evidence=evidence, services={s.name for s in self._services}, specialists=set()
        )
        message = single_agent_context(
            incident,
            self._services,
            max_tool_calls=spec.max_tool_calls,
            lookback_hours=self._settings.investigation.default_lookback_hours,
        )
        agent = Agent(
            spec, llm=self._llm, executor=self._executor, budget=self.budget, hooks=self._hooks
        )
        result = await agent.run(
            message,
            ToolContext(caller="single_agent", evidence=evidence),
            validator=make_validator("report", context),
            task_id="single_agent",
        )
        if result.status == "ok" and isinstance(result.output, ReportDraft):
            draft = result.output
            fallback = False
            termination = "completed"
        else:
            draft = ReportDraft(
                executive_summary=f"The single agent did not produce a report: {result.error}",
                data_gaps=[f"single_agent: {result.error}"],
                recommended_actions=[
                    RecommendedAction(
                        priority="P0",
                        kind="investigate",
                        description="Investigate manually.",
                        rationale="The single-agent run failed.",
                    )
                ],
                next_steps=["Investigate manually."],
            )
            fallback = True
            termination = "agent_failed"
            self.tracer.emit("fallback", component="single_agent", error=result.error)
        status, reason = derive_status(draft.hypotheses, None)
        report = InvestigationReport(
            **draft.model_dump(),
            incident_id=incident.incident_id,
            system="single_agent",
            status=status,
            status_reason=reason,
            termination_reason=termination,
            fallback=fallback,
        )
        wall = time.monotonic() - started
        self.tracer.emit("termination", reason=termination)
        self.tracer.emit("run_end", status=report.status, wall_s=round(wall, 3))
        return InvestigationOutcome(
            run_id=self._run_id,
            system="single_agent",
            incident=incident,
            report=report,
            evidence=evidence,
            metrics=RunMetrics.from_events(self.tracer.events, wall_s=wall),
            events=list(self.tracer.events),
            state=None,
            run_dir=self._run_dir,
        )
