"""Change tools: get_deployments and get_config_changes."""

from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from investigator.backends.base import Backends, ChangeQuery
from investigator.domain.common import UtcDatetime, iso
from investigator.domain.evidence import Evidence
from investigator.harness.tools import Tool, ToolContext, ToolOutput, tool
from investigator.tools.common import validate_window

MAX_WINDOW = timedelta(days=14)


class ChangeWindowInput(BaseModel):
    start: UtcDatetime = Field(description="Window start, ISO-8601 UTC.")
    end: UtcDatetime = Field(description="Window end, ISO-8601 UTC (exclusive).")
    service: str | None = Field(
        default=None,
        description="Optional service filter. Omit it to include every service and shared infrastructure.",
    )


def _warnings(truncated: bool, stale_as_of: datetime | None, count: int) -> list[str]:
    warnings: list[str] = []
    if truncated:
        warnings.append("Results were truncated; narrow the window or filter by service.")
    if stale_as_of is not None:
        warnings.append(
            f"This source appears stale: no records after {iso(stale_as_of)} were returned."
        )
    if count == 0:
        warnings.append("No changes found in this window.")
    return warnings


def build_change_tools(backends: Backends) -> list[Tool]:
    @tool(
        name="get_deployments",
        input_model=ChangeWindowInput,
        description=(
            "List deployments (service, version change, start time, author, status, summary) in a time window. "
            "Call it to find code changes that could have triggered the incident; widen the window to several "
            "days for slow-burn or intermittent symptoms."
        ),
    )
    async def get_deployments(args: ChangeWindowInput, ctx: ToolContext) -> ToolOutput:
        validate_window(args.start, args.end, max_window=MAX_WINDOW)
        result = await backends.changes.list_deployments(
            ChangeQuery(start=args.start, end=args.end, service=args.service)
        )
        evidence = [
            Evidence(
                evidence_id=item.deployment_id,
                kind="deployment",
                service=item.service,
                timestamp=item.started_at,
                summary=(
                    f"{item.service} {item.previous_version} -> {item.version} deployed {iso(item.started_at)} "
                    f"by {item.author} ({item.status}): {item.summary}"
                ),
                source_tool="get_deployments",
                attributes=item.model_dump(mode="json"),
            )
            for item in result.items
        ]
        data = {
            "window": {"start": iso(args.start), "end": iso(args.end)},
            "service": args.service,
            "count": len(result.items),
            "deployments": [item.model_dump(mode="json") for item in result.items],
            "truncated": result.truncated,
            "stale_as_of": iso(result.stale_as_of) if result.stale_as_of else None,
        }
        warnings = _warnings(result.truncated, result.stale_as_of, len(result.items))
        return ToolOutput(data=data, evidence=evidence, partial=result.truncated, warnings=warnings)

    @tool(
        name="get_config_changes",
        input_model=ChangeWindowInput,
        description=(
            "List configuration, feature-flag and infrastructure changes (key, old -> new value, time, author, "
            "source) in a time window. Call it to find runtime changes that could explain the incident; omit the "
            "service filter to include shared infrastructure."
        ),
    )
    async def get_config_changes(args: ChangeWindowInput, ctx: ToolContext) -> ToolOutput:
        validate_window(args.start, args.end, max_window=MAX_WINDOW)
        result = await backends.changes.list_config_changes(
            ChangeQuery(start=args.start, end=args.end, service=args.service)
        )
        evidence = [
            Evidence(
                evidence_id=item.change_id,
                kind="config_change",
                service=item.service,
                timestamp=item.changed_at,
                summary=(
                    f"{item.service} {item.key}: {item.old_value} -> {item.new_value} at {iso(item.changed_at)} "
                    f"by {item.author} ({item.source}): {item.note}"
                ),
                source_tool="get_config_changes",
                attributes=item.model_dump(mode="json"),
            )
            for item in result.items
        ]
        data = {
            "window": {"start": iso(args.start), "end": iso(args.end)},
            "service": args.service,
            "count": len(result.items),
            "config_changes": [item.model_dump(mode="json") for item in result.items],
            "truncated": result.truncated,
            "stale_as_of": iso(result.stale_as_of) if result.stale_as_of else None,
        }
        warnings = _warnings(result.truncated, result.stale_as_of, len(result.items))
        return ToolOutput(data=data, evidence=evidence, partial=result.truncated, warnings=warnings)

    return [get_config_changes, get_deployments]
