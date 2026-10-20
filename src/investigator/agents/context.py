"""Builds each agent's first message from the blackboard (context engineering).

Everything here is deterministic: collections are sorted by stable keys, JSON keys are sorted,
and nothing time-dependent or random is included. Replay tests rely on this.
"""

from datetime import datetime, timedelta

from investigator.domain.common import iso
from investigator.domain.critique import Critique
from investigator.domain.evidence import EvidenceStore, Statement
from investigator.domain.findings import DataGap, FindingRecord
from investigator.domain.hypotheses import HypothesisSet
from investigator.domain.incident import IncidentReport, ServiceInfo
from investigator.domain.plan import Task
from investigator.domain.state import InvestigationState
from investigator.jsonutil import pretty_json

STATUS_RULE = (
    "The report status is derived automatically: top confidence >= 0.75 -> root_cause_identified "
    "(only if the critic accepted, when a critic ran); >= 0.50 -> probable_root_cause; "
    "otherwise inconclusive (next_steps required)."
)


def incident_block(incident: IncidentReport) -> str:
    return "## Incident\n```json\n" + pretty_json(incident.model_dump(mode="json")) + "\n```"


def service_catalog_block(services: list[ServiceInfo]) -> str:
    lines = ["## Service catalog (name | kind | depends on)"]
    for service in sorted(services, key=lambda item: item.name):
        lines.append(f"- {service.name} | {service.kind} | {', '.join(service.depends_on) or '-'}")
    return "\n".join(lines)


def specialist_catalog_block(descriptions: dict[str, str]) -> str:
    lines = ["## Available specialists"]
    lines += [f"- {name}: {text}" for name, text in sorted(descriptions.items())]
    return "\n".join(lines)


def statement_line(statement: Statement) -> str:
    cited = ", ".join(statement.evidence_ids) or "none"
    return f"- [{statement.kind}] {statement.text} (evidence: {cited})"


def finding_block(record: FindingRecord) -> str:
    finding = record.finding
    lines = [
        f"### {record.task_id} ({record.agent}, round {record.round})",
        f"Summary: {finding.summary}",
    ]
    if finding.anomaly_onset is not None:
        lines.append(f"Anomaly onset: {iso(finding.anomaly_onset)}")
    if finding.affected_services:
        lines.append(f"Affected services: {', '.join(sorted(finding.affected_services))}")
    if finding.suspicious_change_ids:
        lines.append(f"Suspicious changes: {', '.join(finding.suspicious_change_ids)}")
    lines.append("Statements:")
    lines += [statement_line(statement) for statement in finding.statements]
    if finding.ruled_out:
        lines.append("Ruled out:")
        lines += [statement_line(statement) for statement in finding.ruled_out]
    if finding.leads:
        lines.append("Leads:")
        lines += [
            f"- for {lead.target_agent}: {lead.question} (services: {', '.join(lead.services) or '-'})"
            for lead in finding.leads
        ]
    if finding.data_gaps:
        lines.append("Data gaps:")
        lines += [f"- {gap.source}: {gap.description}" for gap in finding.data_gaps]
    return "\n".join(lines)


def findings_block(state: InvestigationState) -> str:
    records = state.sorted_findings()
    if not records:
        return "## Findings\n(no findings: every specialist task failed or none ran)"
    return "## Findings\n" + "\n\n".join(finding_block(record) for record in records)


def all_data_gaps(state: InvestigationState) -> list[DataGap]:
    gaps = list(state.data_gaps)
    for record in state.sorted_findings():
        gaps.extend(record.finding.data_gaps)
    unique: dict[tuple[str, str], DataGap] = {}
    for gap in gaps:
        unique.setdefault((gap.source, gap.description), gap)
    return [unique[key] for key in sorted(unique)]


def data_gaps_block(state: InvestigationState) -> str:
    gaps = all_data_gaps(state)
    if not gaps:
        return "## Data gaps\n(none reported)"
    return "## Data gaps\n" + "\n".join(f"- {gap.source}: {gap.description}" for gap in gaps)


def evidence_index_block(store: EvidenceStore, limit: int = 250) -> str:
    items = store.sorted_items()
    lines = [f"## Evidence index ({len(items)} items: id | kind | service | time | summary)"]
    for item in items[:limit]:
        when = iso(item.timestamp) if item.timestamp else "-"
        lines.append(
            f"- {item.evidence_id} | {item.kind} | {item.service or '-'} | {when} | {item.summary}"
        )
    if len(items) > limit:
        lines.append(f"... {len(items) - limit} more items; use get_evidence for details")
    return "\n".join(lines)


def hypotheses_block(hypotheses: HypothesisSet, title: str) -> str:
    return f"## {title}\n```json\n" + pretty_json(hypotheses.model_dump(mode="json")) + "\n```"


def critique_block(critique: Critique, title: str) -> str:
    return f"## {title}\n```json\n" + pretty_json(critique.model_dump(mode="json")) + "\n```"


def tasks_block(state: InvestigationState) -> str:
    lines = ["## Tasks already run (id | status | agent | objective | services)"]
    for task in state.tasks_with_status("done", "failed", "skipped", "pending", "running"):
        services = ", ".join(task.spec.services) or "-"
        lines.append(
            f"- {task.task_id} | {task.status} | {task.spec.agent} | {task.spec.objective} | {services}"
        )
    return "\n".join(lines)


def default_window(incident: IncidentReport, lookback_hours: float) -> tuple[datetime, datetime]:
    return incident.reported_at - timedelta(hours=lookback_hours), incident.reported_at + timedelta(
        minutes=10
    )


def planner_context(
    state: InvestigationState,
    descriptions: dict[str, str],
    *,
    followup: bool,
    max_tasks: int,
    max_rounds: int,
    lookback_hours: float,
) -> str:
    start, end = default_window(state.incident, lookback_hours)
    parts = [
        incident_block(state.incident),
        service_catalog_block(state.services),
        specialist_catalog_block(descriptions),
    ]
    if not followup:
        parts.append(
            f"## Your job\nCreate the initial plan with up to {max_tasks} tasks. A reasonable starting window is "
            f"{iso(start)} to {iso(end)}; widen it when the symptoms suggest a slow or intermittent problem. "
            "Use task ids T1, T2, ... and call submit_plan."
        )
        return "\n\n".join(parts)
    parts.append(tasks_block(state))
    latest_hypotheses = state.latest_hypotheses()
    latest_critique = state.latest_critique()
    if latest_hypotheses is not None:
        parts.append(hypotheses_block(latest_hypotheses, "Current hypotheses"))
    if latest_critique is not None:
        parts.append(critique_block(latest_critique, "Critic review to act on"))
    parts.append(
        f"## Your job\nRound {state.round} of {max_rounds} just finished. Plan at most {max_tasks} NEW tasks for "
        "the next round with new ids T1, T2, ... (refer to existing tasks by their full ids above, such as "
        "'R1-T1'). Do not repeat tasks that already ran. If more evidence is unlikely to change the conclusion, "
        "use action 'conclude' with no tasks. Call submit_plan."
    )
    return "\n\n".join(parts)


def specialist_context(
    state: InvestigationState,
    task: Task,
    *,
    max_tool_calls: int,
    lookback_hours: float,
    previous_error: str | None,
) -> str:
    start, end = default_window(state.incident, lookback_hours)
    window_start = task.spec.window_start or start
    window_end = task.spec.window_end or end
    scope = ", ".join(task.spec.services) or "choose based on the incident"
    lines = [
        f"## Your task ({task.task_id})",
        f"Objective: {task.spec.objective}",
        f"Services in scope: {scope}",
        f"Suggested time window: {iso(window_start)} to {iso(window_end)} (you may widen it)",
    ]
    parts = [incident_block(state.incident), "\n".join(lines)]
    dependencies = sorted(task.spec.depends_on)
    upstream = [state.findings[dep] for dep in dependencies if dep in state.findings]
    failed = [
        dep
        for dep in dependencies
        if dep in state.tasks and state.tasks[dep].status in ("failed", "skipped")
    ]
    if upstream:
        parts.append(
            "## Results of tasks this task depends on\n"
            + "\n\n".join(finding_block(r) for r in upstream)
        )
    if failed:
        failures = "\n".join(f"- {dep}: {state.tasks[dep].error}" for dep in failed)
        parts.append(f"## Dependencies that failed\n{failures}\nProceed with your own discovery.")
    if previous_error:
        parts.append(
            f"## Your previous attempt failed\n{previous_error}\nDo not repeat the same mistake."
        )
    parts.append(
        f"## Budget\nYou may make at most {max_tool_calls} tool calls. Finish by calling submit_findings."
    )
    return "\n\n".join(parts)


def reasoning_context(state: InvestigationState, *, max_rounds: int) -> str:
    parts = [
        incident_block(state.incident),
        service_catalog_block(state.services),
        findings_block(state),
        data_gaps_block(state),
        evidence_index_block(state.evidence),
    ]
    previous = state.latest_hypotheses()
    critique = state.latest_critique()
    if previous is not None:
        parts.append(hypotheses_block(previous, "Your previous hypotheses"))
    if critique is not None:
        parts.append(critique_block(critique, "Critique you must address"))
    parts.append(
        f"## Your job\nRound {state.round} of {max_rounds}. Produce 2-5 ranked hypotheses and call submit_hypotheses."
    )
    return "\n\n".join(parts)


def critic_context(
    state: InvestigationState, hypotheses: HypothesisSet, checks: list[str], *, max_rounds: int
) -> str:
    check_lines = "\n".join(f"- {check}" for check in checks) or "- none"
    previous = [
        critique_block(c, f"Your critique from round {index + 1}")
        for index, c in enumerate(state.critiques)
    ]
    parts = [
        incident_block(state.incident),
        findings_block(state),
        data_gaps_block(state),
        evidence_index_block(state.evidence),
        hypotheses_block(hypotheses, "Hypotheses to review"),
        f"## Automated checks\n{check_lines}",
        *previous,
    ]
    rounds_left = max_rounds - state.round
    parts.append(
        f"## Your job\nRound {state.round} of {max_rounds}; {rounds_left} round(s) left after this one. "
        "Review the hypotheses and call submit_critique."
    )
    return "\n\n".join(parts)


def reporter_context(state: InvestigationState, *, critic_accepted: bool | None) -> str:
    latest = state.latest_hypotheses()
    critique = state.latest_critique()
    parts = [
        incident_block(state.incident),
        findings_block(state),
        data_gaps_block(state),
        evidence_index_block(state.evidence),
    ]
    parts.append(
        hypotheses_block(latest, "Final hypotheses")
        if latest
        else "## Final hypotheses\n(none were produced)"
    )
    if critique is not None:
        parts.append(critique_block(critique, "Final critic review"))
    accepted = "no critic ran" if critic_accepted is None else ("yes" if critic_accepted else "no")
    parts.append(
        f"## Investigation status\nTermination reason: {state.termination_reason}\n"
        f"Critic accepted the conclusion: {accepted}\n{STATUS_RULE}"
    )
    parts.append("## Your job\nWrite the final report and call submit_report.")
    return "\n\n".join(parts)


def single_agent_context(
    incident: IncidentReport,
    services: list[ServiceInfo],
    *,
    max_tool_calls: int,
    lookback_hours: float,
) -> str:
    start, end = default_window(incident, lookback_hours)
    job = (
        f"## Your job\nInvestigate this incident. A reasonable starting window is {iso(start)} to {iso(end)}; "
        f"widen it when needed. You may make at most {max_tool_calls} tool calls. {STATUS_RULE} "
        "Call submit_report when done."
    )
    return "\n\n".join([incident_block(incident), service_catalog_block(services), job])
