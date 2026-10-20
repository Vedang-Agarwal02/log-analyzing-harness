"""Render an InvestigationReport as Markdown. Evidence details come from the evidence store, not model text."""

from investigator.domain.common import iso
from investigator.domain.evidence import EvidenceStore
from investigator.domain.incident import IncidentReport
from investigator.domain.report import HIGH_CONFIDENCE, MEDIUM_CONFIDENCE, InvestigationReport
from investigator.harness.tracing import RunMetrics

STATUS_LABEL = {
    "root_cause_identified": "Root cause identified",
    "probable_root_cause": "Probable root cause",
    "inconclusive": "Inconclusive",
}


def confidence_label(value: float) -> str:
    if value >= HIGH_CONFIDENCE:
        return "high"
    return "medium" if value >= MEDIUM_CONFIDENCE else "low"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _evidence_lines(store: EvidenceStore, ids: list[str]) -> list[str]:
    if not ids:
        return ["  - none"]
    lines: list[str] = []
    for evidence_id in ids:
        item = store.get(evidence_id)
        detail = item.summary if item is not None else "(not found in the evidence store)"
        lines.append(f"  - `{evidence_id}`: {detail}")
    return lines


def _cites(ids: list[str]) -> str:
    return ", ".join(f"`{evidence_id}`" for evidence_id in ids) or "no evidence"


def render_report(
    report: InvestigationReport,
    incident: IncidentReport,
    evidence: EvidenceStore,
    metrics: RunMetrics | None = None,
) -> str:
    header = f"**System:** {report.system} | **Termination:** {report.termination_reason}"
    if report.fallback:
        header += " | **Automated fallback report**"
    lines = [
        f"# Incident investigation: {incident.incident_id} - {incident.title}",
        "",
        f"**Status:** {STATUS_LABEL[report.status]} ({report.status_reason})",
        header,
        "",
        "## Executive summary",
        "",
        report.executive_summary,
        "",
        "## Ranked hypotheses",
        "",
    ]
    if not report.hypotheses:
        lines += ["No hypotheses were produced.", ""]
    for rank, item in enumerate(report.hypotheses, start=1):
        lines += [
            f"### {rank}. {item.title} (confidence {item.confidence:.2f}, {confidence_label(item.confidence)})",
            "",
            f"- **Root-cause service:** {item.root_cause_service}",
            f"- **Category:** {item.category}",
            f"- **Trigger:** {item.trigger_change_id or 'none identified'}",
            f"- **Mechanism:** {item.mechanism}",
            "- **Supporting evidence:**",
            *_evidence_lines(evidence, item.supporting_evidence),
            "- **Contradicting evidence:**",
            *_evidence_lines(evidence, item.contradicting_evidence),
        ]
        if item.assumptions:
            lines += ["- **Assumptions:**", *[f"  - {text}" for text in item.assumptions]]
        lines.append("")
    lines += ["## Facts (verified observations)", ""]
    lines += [f"- {fact.text} ({_cites(fact.evidence_ids)})" for fact in report.facts] or ["- none"]
    lines += ["", "## Assumptions", ""] + (
        [f"- {text}" for text in report.assumptions] or ["- none stated"]
    )
    lines += ["", "## Data gaps", ""] + (
        [f"- {text}" for text in report.data_gaps] or ["- none reported"]
    )
    timeline = sorted(report.timeline, key=lambda event: event.at)
    lines += ["", "## Timeline", ""]
    lines += [
        f"- {iso(event.at)}: {event.description} ({_cites(event.evidence_ids)})"
        for event in timeline
    ] or ["- not provided"]
    lines += [
        "",
        "## Recommended actions",
        "",
        "| Priority | Kind | Action | Rationale | Evidence |",
        "|---|---|---|---|---|",
    ]
    for action in report.recommended_actions:
        cited = ", ".join(action.evidence_ids) or "-"
        lines.append(
            f"| {action.priority} | {action.kind} | {_cell(action.description)} | {_cell(action.rationale)} | {cited} |"
        )
    if report.next_steps:
        lines += ["", "## Next steps", ""] + [f"- {step}" for step in report.next_steps]
    if metrics is not None:
        usage = metrics.usage
        lines += [
            "",
            "## Investigation statistics",
            "",
            f"- LLM calls: {metrics.llm_calls}; tool calls: {metrics.tool_calls} "
            f"({metrics.tool_errors} failed, {metrics.tool_attempts} attempts)",
            f"- Tokens: {usage.input_tokens} input, {usage.output_tokens} output, "
            f"{usage.cache_read_tokens} cache read, {usage.cache_write_tokens} cache write",
            f"- Wall-clock: {metrics.wall_s:.1f}s",
        ]
    cited_ids = sorted(
        {
            e
            for item in report.hypotheses
            for e in [*item.supporting_evidence, *item.contradicting_evidence]
        }
        | {e for fact in report.facts for e in fact.evidence_ids}
    )
    lines += ["", "## Evidence appendix", ""]
    for evidence_id in cited_ids:
        stored = evidence.get(evidence_id)
        if stored is not None:
            when = iso(stored.timestamp) if stored.timestamp else "-"
            lines.append(
                f"- `{evidence_id}` [{stored.kind}, {stored.service or '-'}, {when}, via {stored.source_tool}]: {stored.summary}"
            )
    if not cited_ids:
        lines.append("- none")
    return "\n".join(lines) + "\n"
