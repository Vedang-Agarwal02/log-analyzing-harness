"""search_incident_history: keyword search over past incident postmortems."""

from pydantic import BaseModel, Field

from investigator.backends.base import Backends
from investigator.domain.common import parse_iso
from investigator.domain.evidence import Evidence
from investigator.harness.tools import Tool, ToolContext, ToolOutput, tool
from investigator.tools.common import clip


class HistoryInput(BaseModel):
    query: str = Field(
        description="Keywords for symptoms or suspected causes, e.g. 'connection pool timeout portfolio'."
    )
    service: str | None = Field(
        default=None, description="Optional service to boost matching incidents."
    )
    limit: int = Field(default=5, ge=1, le=10)


def build_history_tools(backends: Backends) -> list[Tool]:
    @tool(
        name="search_incident_history",
        input_model=HistoryInput,
        description=(
            "Search past incident postmortems by keywords. Use the results as hints for hypotheses and checks to "
            "run; a past incident is NOT evidence about the current one."
        ),
    )
    async def search_incident_history(args: HistoryInput, ctx: ToolContext) -> ToolOutput:
        hits = await backends.history.search_incidents(args.query, args.service, args.limit)
        evidence = [
            Evidence(
                evidence_id=hit.incident.incident_id,
                kind="incident_history",
                service=hit.incident.services[0] if hit.incident.services else None,
                timestamp=parse_iso(f"{hit.incident.date}T00:00:00Z"),
                summary=f"{hit.incident.date} {hit.incident.title} (root cause: {clip(hit.incident.root_cause, 120)})",
                source_tool="search_incident_history",
                attributes=hit.incident.model_dump(mode="json"),
            )
            for hit in hits
        ]
        incidents = [{**hit.incident.model_dump(mode="json"), "score": hit.score} for hit in hits]
        warnings = [] if hits else ["No similar past incidents found."]
        return ToolOutput(
            data={"query": args.query, "incidents": incidents}, evidence=evidence, warnings=warnings
        )

    return [search_incident_history]
