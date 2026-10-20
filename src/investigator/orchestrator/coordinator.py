"""The coordinator owns the blackboard and runs: plan -> gather -> hypothesize -> critique -> (replan) -> report.

The LLM planner decides WHAT to investigate. This code decides HOW: scheduling, limits,
retries, termination and fallbacks. Only this class writes to the InvestigationState.
"""

import asyncio
import time
import traceback
from pathlib import Path

from investigator.agents.catalog import AgentCatalog
from investigator.agents.context import (
    critic_context,
    default_window,
    planner_context,
    reasoning_context,
    reporter_context,
    specialist_context,
)
from investigator.agents.outputs import ValidationContext, make_validator
from investigator.domain.critique import Critique
from investigator.domain.findings import DataGap, FindingDraft, FindingRecord
from investigator.domain.hypotheses import HypothesisSet
from investigator.domain.incident import IncidentReport, ServiceInfo
from investigator.domain.plan import PlanDraft, Task, TaskOrigin, TaskSpec
from investigator.domain.report import InvestigationReport, ReportDraft, derive_status
from investigator.domain.state import FailureRecord, InvestigationState, Phase
from investigator.harness.agent import Agent, AgentSpec
from investigator.harness.budget import Budget
from investigator.harness.executor import ToolExecutor
from investigator.harness.hooks import HookRunner
from investigator.harness.llm import LLMClient
from investigator.harness.tools import ToolContext, ToolRegistry
from investigator.harness.tracing import RunMetrics, Tracer
from investigator.orchestrator.outcome import InvestigationOutcome
from investigator.orchestrator.policies import (
    fallback_plan,
    fallback_report,
    run_checks,
    task_signature,
    termination_decision,
)
from investigator.orchestrator.scheduler import Scheduler
from investigator.settings import Settings


class Coordinator:
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
        self._cfg = settings.investigation
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
        self._scheduler = Scheduler(max_parallel=self._cfg.max_parallel_tasks)

    # ------------------------------------------------------------------ helpers

    def _agent(self, spec: AgentSpec) -> Agent:
        return Agent(
            spec, llm=self._llm, executor=self._executor, budget=self.budget, hooks=self._hooks
        )

    def _validation(
        self,
        state: InvestigationState,
        *,
        existing_task_ids: set[str] | None = None,
        initial_plan: bool = False,
        max_tasks: int = 6,
    ) -> ValidationContext:
        return ValidationContext(
            evidence=state.evidence,
            services=state.service_names(),
            specialists=set(self._catalog.specialists),
            existing_task_ids=existing_task_ids or set(),
            initial_plan=initial_plan,
            max_tasks=max_tasks,
        )

    def _phase(self, state: InvestigationState, phase: Phase) -> None:
        state.phase = phase
        self.tracer.emit("phase", name=phase, round=state.round)
        self._checkpoint(state)

    def _checkpoint(self, state: InvestigationState) -> None:
        if self._run_dir is not None:
            (self._run_dir / "state.json").write_text(
                state.model_dump_json(indent=1), encoding="utf-8"
            )

    # ------------------------------------------------------------------ main loop

    async def investigate(self, incident: IncidentReport) -> InvestigationOutcome:
        started = time.monotonic()
        state = InvestigationState(run_id=self._run_id, incident=incident, services=self._services)
        self.tracer.emit(
            "run_start",
            system="multi_agent",
            incident_id=incident.incident_id,
            model=self._settings.llm.model,
        )
        last_critique: Critique | None = None
        try:
            await self._plan(state, followup=False)
            for round_number in range(1, self._cfg.max_rounds + 1):
                state.round = round_number
                self._phase(state, "gathering")
                await self._scheduler.run(state, lambda task: self._run_task(state, task))
                if self.budget.exhausted():
                    state.termination_reason = "budget_exhausted"
                    break
                self._phase(state, "hypothesizing")
                hypotheses = await self._reason(state)
                if hypotheses is None:
                    state.termination_reason = (
                        "budget_exhausted" if self.budget.exhausted() else "reasoning_failed"
                    )
                    break
                if not self._cfg.critic_enabled:
                    state.termination_reason = "critic_disabled"
                    break
                self._phase(state, "critiquing")
                checks = run_checks(state, hypotheses) if self._cfg.checks_enabled else []
                self.tracer.emit("checks", round=round_number, checks=checks)
                critique = await self._critique(state, hypotheses, checks)
                if critique is None:
                    state.termination_reason = (
                        "budget_exhausted" if self.budget.exhausted() else "critic_failed"
                    )
                    break
                last_critique = critique
                decision = termination_decision(
                    state,
                    critique,
                    max_rounds=self._cfg.max_rounds,
                    budget=self.budget,
                    stagnation_delta=self._cfg.stagnation_delta,
                )
                if decision.stop:
                    state.termination_reason = decision.reason
                    break
                if critique.verdict == "revise":
                    continue
                if await self._plan(state, followup=True) == 0:
                    state.termination_reason = state.termination_reason or "no_new_tasks"
                    break
            else:
                state.termination_reason = state.termination_reason or "max_rounds_reached"
        except Exception as exc:  # never lose the report because of a bug in one phase
            state.failures.append(
                FailureRecord(component="coordinator", error=f"{type(exc).__name__}: {exc}")
            )
            state.termination_reason = state.termination_reason or "internal_error"
            self.tracer.emit("error", component="coordinator", error=traceback.format_exc())
        critic_accepted: bool | None = None
        if self._cfg.critic_enabled:
            critic_accepted = last_critique is not None and last_critique.verdict == "accept"
        self._phase(state, "reporting")
        report = await self._report(state, critic_accepted=critic_accepted)
        state.phase = "done"
        self._checkpoint(state)
        wall = time.monotonic() - started
        self.tracer.emit("termination", reason=state.termination_reason)
        self.tracer.emit(
            "run_end",
            status=report.status,
            rounds=state.round,
            tasks=len(state.tasks),
            wall_s=round(wall, 3),
        )
        return InvestigationOutcome(
            run_id=self._run_id,
            system="multi_agent",
            incident=incident,
            report=report,
            evidence=state.evidence,
            metrics=RunMetrics.from_events(self.tracer.events, wall_s=wall),
            events=list(self.tracer.events),
            state=state,
            run_dir=self._run_dir,
        )

    # ------------------------------------------------------------------ planning

    async def _plan(self, state: InvestigationState, *, followup: bool) -> int:
        self._phase(state, "replanning" if followup else "planning")
        limit = self._cfg.max_followup_tasks if followup else self._cfg.max_initial_tasks
        max_tasks = max(0, min(limit, self._cfg.max_tasks_total - len(state.tasks)))
        if followup and max_tasks == 0:
            state.termination_reason = "task_limit_reached"
            return 0
        message = planner_context(
            state,
            self._catalog.descriptions,
            followup=followup,
            max_tasks=max_tasks,
            max_rounds=self._cfg.max_rounds,
            lookback_hours=self._cfg.default_lookback_hours,
        )
        context = self._validation(
            state,
            existing_task_ids=set(state.tasks),
            initial_plan=not followup,
            max_tasks=max_tasks,
        )
        result = await self._agent(self._catalog.planner).run(
            message,
            ToolContext(caller="planner", evidence=state.evidence),
            validator=make_validator("plan", context),
            task_id=f"planner-r{state.round}",
        )
        origin: TaskOrigin = "followup" if followup else "plan"
        if result.status == "ok" and isinstance(result.output, PlanDraft):
            plan = result.output
        else:
            state.failures.append(
                FailureRecord(component="planner", error=result.error or "unknown error")
            )
            if followup:
                state.termination_reason = "planner_failed"
                return 0
            window = default_window(state.incident, self._cfg.default_lookback_hours)
            plan = fallback_plan(state, set(self._catalog.specialists), window)
            origin = "fallback"
            self.tracer.emit("fallback", component="planner", error=result.error)
        if plan.action == "conclude":
            state.termination_reason = "planner_concluded"
            return 0
        return self._add_tasks(state, plan, origin)

    def _add_tasks(self, state: InvestigationState, plan: PlanDraft, origin: TaskOrigin) -> int:
        round_number = state.round + 1
        mapping = {spec.task_id: f"R{round_number}-{spec.task_id}" for spec in plan.tasks}
        known = {
            task_signature(t.spec.agent, t.spec.services, t.spec.objective)
            for t in state.tasks.values()
        }
        accepted: list[TaskSpec] = []
        for spec in plan.tasks:
            signature = task_signature(spec.agent, spec.services, spec.objective)
            if signature in known:
                self.tracer.emit("task_deduplicated", task=spec.task_id, objective=spec.objective)
                continue
            known.add(signature)
            accepted.append(spec)
        accepted_ids = {mapping[spec.task_id] for spec in accepted}
        for spec in accepted:
            dependencies = [mapping.get(dep, dep) for dep in spec.depends_on]
            dependencies = [
                dep for dep in dependencies if dep in state.tasks or dep in accepted_ids
            ]
            task_id = mapping[spec.task_id]
            stored = spec.model_copy(update={"task_id": task_id, "depends_on": dependencies})
            state.tasks[task_id] = Task(
                task_id=task_id, round=round_number, origin=origin, spec=stored
            )
            self.tracer.emit(
                "task_added",
                task_id=task_id,
                agent=spec.agent,
                objective=spec.objective,
                depends_on=dependencies,
                origin=origin,
            )
        return len(accepted)

    # ------------------------------------------------------------------ phases

    async def _run_task(self, state: InvestigationState, task: Task) -> None:
        self.tracer.emit(
            "task_start", task_id=task.task_id, agent=task.spec.agent, objective=task.spec.objective
        )
        spec = self._catalog.specialists.get(task.spec.agent)
        if spec is None:
            task.status = "failed"
            task.error = f"unknown specialist {task.spec.agent!r}"
            self.tracer.emit("task_end", task_id=task.task_id, status="failed", error=task.error)
            return
        error: str | None = None
        for attempt in range(1, self._cfg.max_task_attempts + 1):
            task.attempts = attempt
            message = specialist_context(
                state,
                task,
                max_tool_calls=spec.max_tool_calls,
                lookback_hours=self._cfg.default_lookback_hours,
                previous_error=error,
            )
            try:
                result = await asyncio.wait_for(
                    self._agent(spec).run(
                        message,
                        ToolContext(caller=task.task_id, evidence=state.evidence),
                        validator=make_validator("finding", self._validation(state)),
                        task_id=task.task_id,
                    ),
                    timeout=self._cfg.task_timeout_s,
                )
            except TimeoutError:
                error = f"task timed out after {self._cfg.task_timeout_s:.0f}s"
            else:
                if result.status == "ok" and isinstance(result.output, FindingDraft):
                    state.findings[task.task_id] = FindingRecord(
                        task_id=task.task_id,
                        agent=task.spec.agent,
                        round=task.round,
                        finding=result.output,
                    )
                    task.status = "done"
                    task.error = None
                    self.tracer.emit(
                        "task_end", task_id=task.task_id, status="done", attempts=attempt
                    )
                    return
                error = result.error or "unknown error"
            if self.budget.exhausted():
                break
        task.status = "failed"
        task.error = error
        state.failures.append(
            FailureRecord(
                component=f"agent:{task.spec.agent}",
                task_id=task.task_id,
                error=error or "unknown error",
            )
        )
        state.data_gaps.append(
            DataGap(
                source=f"{task.spec.agent} task {task.task_id}",
                description=f"Task failed after {task.attempts} attempt(s): {error}. Not covered: {task.spec.objective}",
            )
        )
        self.tracer.emit(
            "task_end", task_id=task.task_id, status="failed", attempts=task.attempts, error=error
        )

    async def _reason(self, state: InvestigationState) -> HypothesisSet | None:
        for _ in range(2):
            result = await self._agent(self._catalog.reasoning).run(
                reasoning_context(state, max_rounds=self._cfg.max_rounds),
                ToolContext(caller=f"reasoning-r{state.round}", evidence=state.evidence),
                validator=make_validator("hypotheses", self._validation(state)),
                task_id=f"reasoning-r{state.round}",
            )
            if result.status == "ok" and isinstance(result.output, HypothesisSet):
                state.hypothesis_rounds.append(result.output)
                top = result.output.hypotheses[0]
                self.tracer.emit(
                    "hypotheses",
                    round=state.round,
                    top_title=top.title,
                    top_service=top.root_cause_service,
                    top_category=top.category,
                    top_confidence=top.confidence,
                    count=len(result.output.hypotheses),
                )
                return result.output
            state.failures.append(
                FailureRecord(component="reasoning", error=result.error or "unknown error")
            )
            if self.budget.exhausted():
                break
        return None

    async def _critique(
        self, state: InvestigationState, hypotheses: HypothesisSet, checks: list[str]
    ) -> Critique | None:
        for _ in range(2):
            result = await self._agent(self._catalog.critic).run(
                critic_context(state, hypotheses, checks, max_rounds=self._cfg.max_rounds),
                ToolContext(caller=f"critic-r{state.round}", evidence=state.evidence),
                validator=make_validator("critique", self._validation(state)),
                task_id=f"critic-r{state.round}",
            )
            if result.status == "ok" and isinstance(result.output, Critique):
                state.critiques.append(result.output)
                self.tracer.emit(
                    "critique",
                    round=state.round,
                    verdict=result.output.verdict,
                    issues=len(result.output.issues),
                    requests=len(result.output.evidence_requests),
                )
                return result.output
            state.failures.append(
                FailureRecord(component="critic", error=result.error or "unknown error")
            )
            if self.budget.exhausted():
                break
        return None

    async def _report(
        self, state: InvestigationState, *, critic_accepted: bool | None
    ) -> InvestigationReport:
        draft: ReportDraft | None = None
        for _ in range(2):
            result = await self._agent(self._catalog.reporter).run(
                reporter_context(state, critic_accepted=critic_accepted),
                ToolContext(caller="reporter", evidence=state.evidence),
                validator=make_validator("report", self._validation(state)),
                task_id="reporter",
            )
            if result.status == "ok" and isinstance(result.output, ReportDraft):
                draft = result.output
                break
            state.failures.append(
                FailureRecord(component="reporter", error=result.error or "unknown error")
            )
            if self.budget.exhausted(use_reserve=True):
                break
        fallback = draft is None
        if draft is None:
            draft = fallback_report(state, reason="the reporting agent failed")
            self.tracer.emit("fallback", component="reporter")
        status, reason = derive_status(draft.hypotheses, critic_accepted)
        return InvestigationReport(
            **draft.model_dump(),
            incident_id=state.incident.incident_id,
            system="multi_agent",
            status=status,
            status_reason=reason,
            termination_reason=state.termination_reason or "completed",
            fallback=fallback,
        )
