"""Output tools (submit_*) and the semantic validators that keep outputs grounded and consistent."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from investigator.domain.critique import Critique
from investigator.domain.evidence import EvidenceStore, Statement
from investigator.domain.findings import FindingDraft
from investigator.domain.hypotheses import HypothesisSet
from investigator.domain.plan import PlanDraft
from investigator.domain.report import MEDIUM_CONFIDENCE, ReportDraft
from investigator.harness.agent import OutputSpec, OutputValidator

OutputKind = Literal["plan", "finding", "hypotheses", "critique", "report"]

OUTPUT_SPECS: dict[str, OutputSpec] = {
    "plan": OutputSpec(
        "submit_plan",
        "Submit your investigation plan, or decide to conclude. Call it exactly once, when the plan is ready.",
        PlanDraft,
    ),
    "finding": OutputSpec(
        "submit_findings",
        "Submit your findings for this task. Call it exactly once, after gathering evidence. "
        "Every observation must cite evidence IDs returned by tools.",
        FindingDraft,
    ),
    "hypotheses": OutputSpec(
        "submit_hypotheses",
        "Submit 2-5 ranked root-cause hypotheses (most likely first, confidence non-increasing). Call it exactly once.",
        HypothesisSet,
    ),
    "critique": OutputSpec(
        "submit_critique", "Submit your critique and verdict. Call it exactly once.", Critique
    ),
    "report": OutputSpec(
        "submit_report", "Submit the final investigation report. Call it exactly once.", ReportDraft
    ),
}

CHANGE_KINDS = {"deployment", "config_change"}


@dataclass
class ValidationContext:
    evidence: EvidenceStore
    services: set[str]
    specialists: set[str]
    existing_task_ids: set[str] = field(default_factory=set)
    initial_plan: bool = False
    max_tasks: int = 6


def _ids(problems: list[str], ctx: ValidationContext, where: str, ids: Iterable[str]) -> None:
    missing = ctx.evidence.missing(ids)
    if missing:
        problems.append(
            f"{where}: unknown evidence id(s) {', '.join(missing)}; cite only IDs returned by tools"
        )


def _service(problems: list[str], ctx: ValidationContext, where: str, name: str) -> None:
    if name not in ctx.services:
        problems.append(f"{where}: unknown service {name!r}; use an exact service catalog name")


def _change(problems: list[str], ctx: ValidationContext, where: str, change_id: str | None) -> None:
    if change_id is None:
        return
    item = ctx.evidence.get(change_id)
    if item is None or item.kind not in CHANGE_KINDS:
        problems.append(
            f"{where}: trigger {change_id!r} must be the evidence ID of a deployment or config change returned by a tool"
        )


def _statements(
    problems: list[str],
    ctx: ValidationContext,
    where: str,
    statements: list[Statement],
    *,
    facts: bool = False,
) -> None:
    for index, statement in enumerate(statements):
        label = f"{where}[{index}]"
        if facts and statement.kind != "observation":
            problems.append(f"{label}: facts must have kind 'observation'")
        if statement.kind == "observation" and not statement.evidence_ids:
            problems.append(f"{label}: an observation must cite at least one evidence ID")
        _ids(problems, ctx, label, statement.evidence_ids)


def _non_increasing(problems: list[str], where: str, confidences: list[float]) -> None:
    if any(later > earlier for earlier, later in zip(confidences, confidences[1:], strict=False)):
        problems.append(
            f"{where}: rank hypotheses by confidence (most likely first, non-increasing)"
        )


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        if state.get(node) == 0:
            return True
        if state.get(node) == 1:
            return False
        state[node] = 0
        if any(visit(child) for child in graph.get(node, [])):
            return True
        state[node] = 1
        return False

    return any(visit(node) for node in graph)


def validate_plan(plan: PlanDraft, ctx: ValidationContext) -> list[str]:
    problems: list[str] = []
    if ctx.initial_plan and plan.action != "investigate":
        problems.append("the initial plan must use action 'investigate'")
    if plan.action == "investigate" and not plan.tasks:
        problems.append("an 'investigate' plan needs at least one task")
    if plan.action == "conclude" and plan.tasks:
        problems.append("a 'conclude' plan must not contain tasks")
    if len(plan.tasks) > ctx.max_tasks:
        problems.append(
            f"at most {ctx.max_tasks} tasks are allowed now; you proposed {len(plan.tasks)}"
        )
    local_ids = [task.task_id for task in plan.tasks]
    if len(local_ids) != len(set(local_ids)):
        problems.append("task ids must be unique")
    local = set(local_ids)
    for task in plan.tasks:
        where = f"task {task.task_id}"
        if task.agent not in ctx.specialists:
            choices = ", ".join(sorted(ctx.specialists))
            problems.append(f"{where}: unknown specialist {task.agent!r}; choose from {choices}")
        for name in task.services:
            _service(problems, ctx, where, name)
        if task.window_start and task.window_end and task.window_start >= task.window_end:
            problems.append(f"{where}: window_start must be before window_end")
        if task.task_id in ctx.existing_task_ids:
            problems.append(f"{where}: task id already used; choose a new id")
        for dependency in task.depends_on:
            if dependency == task.task_id:
                problems.append(f"{where}: a task cannot depend on itself")
            elif dependency not in local and dependency not in ctx.existing_task_ids:
                problems.append(f"{where}: depends_on refers to unknown task {dependency!r}")
    graph = {
        task.task_id: [d for d in task.depends_on if d in local and d != task.task_id]
        for task in plan.tasks
    }
    if _has_cycle(graph):
        problems.append("depends_on contains a cycle")
    return problems


def validate_finding(finding: FindingDraft, ctx: ValidationContext) -> list[str]:
    problems: list[str] = []
    _statements(problems, ctx, "statements", finding.statements)
    _statements(problems, ctx, "ruled_out", finding.ruled_out)
    for change_id in finding.suspicious_change_ids:
        _change(problems, ctx, "suspicious_change_ids", change_id)
    for name in finding.affected_services:
        _service(problems, ctx, "affected_services", name)
    for index, lead in enumerate(finding.leads):
        if lead.target_agent not in ctx.specialists:
            problems.append(f"leads[{index}]: unknown specialist {lead.target_agent!r}")
        for name in lead.services:
            _service(problems, ctx, f"leads[{index}]", name)
    return problems


def validate_hypotheses(hypotheses: HypothesisSet, ctx: ValidationContext) -> list[str]:
    problems: list[str] = []
    ids = [item.hypothesis_id for item in hypotheses.hypotheses]
    if len(ids) != len(set(ids)):
        problems.append("hypothesis ids must be unique")
    _non_increasing(problems, "hypotheses", [item.confidence for item in hypotheses.hypotheses])
    for item in hypotheses.hypotheses:
        where = f"hypothesis {item.hypothesis_id}"
        _service(problems, ctx, where, item.root_cause_service)
        _ids(problems, ctx, where, [*item.supporting_evidence, *item.contradicting_evidence])
        _change(problems, ctx, where, item.trigger_change_id)
        for check in item.checks:
            if check.target_agent not in ctx.specialists:
                problems.append(f"{where}: checks name unknown specialist {check.target_agent!r}")
    return problems


def validate_critique(critique: Critique, ctx: ValidationContext) -> list[str]:
    problems: list[str] = []
    if critique.verdict == "investigate" and not critique.evidence_requests:
        problems.append("verdict 'investigate' needs at least one evidence request")
    if len(critique.evidence_requests) > 4:
        problems.append("at most 4 evidence requests are allowed")
    for index, request in enumerate(critique.evidence_requests):
        if request.target_agent not in ctx.specialists:
            problems.append(
                f"evidence_requests[{index}]: unknown specialist {request.target_agent!r}"
            )
        for name in request.services:
            _service(problems, ctx, f"evidence_requests[{index}]", name)
    for index, issue in enumerate(critique.issues):
        _ids(problems, ctx, f"issues[{index}]", issue.evidence_ids)
    return problems


def validate_report(report: ReportDraft, ctx: ValidationContext) -> list[str]:
    problems: list[str] = []
    _non_increasing(problems, "hypotheses", [item.confidence for item in report.hypotheses])
    for index, item in enumerate(report.hypotheses):
        where = f"hypotheses[{index}]"
        _service(problems, ctx, where, item.root_cause_service)
        _ids(problems, ctx, where, [*item.supporting_evidence, *item.contradicting_evidence])
        _change(problems, ctx, where, item.trigger_change_id)
    _statements(problems, ctx, "facts", report.facts, facts=True)
    for index, action in enumerate(report.recommended_actions):
        _ids(problems, ctx, f"recommended_actions[{index}]", action.evidence_ids)
    for index, event in enumerate(report.timeline):
        _ids(problems, ctx, f"timeline[{index}]", event.evidence_ids)
    top = report.hypotheses[0].confidence if report.hypotheses else 0.0
    if top < MEDIUM_CONFIDENCE and not report.next_steps:
        problems.append(
            "when the top confidence is below 0.50 the report is inconclusive and must list next_steps"
        )
    return problems


def make_validator(kind: OutputKind, ctx: ValidationContext) -> OutputValidator:
    def validate(model: BaseModel) -> list[str]:
        if kind == "plan" and isinstance(model, PlanDraft):
            return validate_plan(model, ctx)
        if kind == "finding" and isinstance(model, FindingDraft):
            return validate_finding(model, ctx)
        if kind == "hypotheses" and isinstance(model, HypothesisSet):
            return validate_hypotheses(model, ctx)
        if kind == "critique" and isinstance(model, Critique):
            return validate_critique(model, ctx)
        if kind == "report" and isinstance(model, ReportDraft):
            return validate_report(model, ctx)
        return [f"unexpected output type {type(model).__name__} for {kind}"]

    return validate
