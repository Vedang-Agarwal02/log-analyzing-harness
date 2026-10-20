"""query_logs: search one service's logs, clustered into patterns, with a few raw samples."""

from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field

from investigator.backends.analysis import normalize_message, pattern_id
from investigator.backends.base import Backends, LogQuery, LogRecord
from investigator.domain.common import LogLevel, UtcDatetime, iso
from investigator.domain.evidence import Evidence
from investigator.harness.tools import Tool, ToolContext, ToolOutput, tool
from investigator.tools.common import clip, validate_window

MAX_WINDOW = timedelta(days=4)
MAX_PATTERNS = 15


class QueryLogsInput(BaseModel):
    service: str = Field(
        description="Service name exactly as in the service catalog, e.g. 'orders-service'."
    )
    start: UtcDatetime = Field(description="Window start, ISO-8601 UTC.")
    end: UtcDatetime = Field(description="Window end, ISO-8601 UTC (exclusive).")
    level: LogLevel | None = Field(
        default=None,
        description="Minimum level: 'WARN' returns WARN and ERROR. Omit for all levels.",
    )
    contains: str | None = Field(
        default=None, description="Case-insensitive substring the message must contain."
    )
    max_samples: int = Field(
        default=8, ge=1, le=30, description="Raw log lines to include as samples."
    )


def _pick_samples(records: list[LogRecord], firsts: list[LogRecord], limit: int) -> list[LogRecord]:
    chosen: dict[str, LogRecord] = {}
    for record in firsts:
        if len(chosen) >= limit:
            break
        chosen[record.log_id] = record
    for record in reversed(records):
        if len(chosen) >= limit:
            break
        if record.level in ("WARN", "ERROR"):
            chosen.setdefault(record.log_id, record)
    return sorted(chosen.values(), key=lambda record: (record.ts, record.log_id))


def build_log_tools(backends: Backends) -> list[Tool]:
    @tool(
        name="query_logs",
        input_model=QueryLogsInput,
        description=(
            "Search one service's logs in a time window. Similar lines are clustered into patterns with "
            "counts and first/last-seen times, plus a few raw sample lines. Call it to find messages that "
            "explain an anomaly (exceptions, timeouts, connection or resource errors) or to check whether a "
            "pattern already existed before the incident. Use level='WARN' to focus on problems."
        ),
    )
    async def query_logs(args: QueryLogsInput, ctx: ToolContext) -> ToolOutput:
        validate_window(args.start, args.end, max_window=MAX_WINDOW)
        query = LogQuery(
            service=args.service,
            start=args.start,
            end=args.end,
            min_level=args.level,
            contains=args.contains,
        )
        result = await backends.logs.search_logs(query)
        groups: dict[str, dict[str, Any]] = {}
        for record in result.records:
            template = normalize_message(record.message)
            pid = pattern_id(record.service, record.level, template)
            group = groups.get(pid)
            if group is None:
                groups[pid] = {
                    "pattern_id": pid,
                    "level": record.level,
                    "template": template,
                    "count": 1,
                    "first": record,
                    "last_seen": record.ts,
                }
            else:
                group["count"] += 1
                group["last_seen"] = max(group["last_seen"], record.ts)
        ordered = sorted(groups.values(), key=lambda group: (-group["count"], group["pattern_id"]))
        top = ordered[:MAX_PATTERNS]
        samples = _pick_samples(result.records, [group["first"] for group in top], args.max_samples)
        evidence = [
            Evidence(
                evidence_id=group["pattern_id"],
                kind="log_pattern",
                service=args.service,
                timestamp=group["first"].ts,
                summary=(
                    f'{group["count"]}x {group["level"]} "{clip(group["template"], 140)}" in {args.service} '
                    f"({iso(group['first'].ts)} to {iso(group['last_seen'])})"
                ),
                source_tool="query_logs",
                attributes={
                    "template": group["template"],
                    "level": group["level"],
                    "count": group["count"],
                    "first_seen": iso(group["first"].ts),
                    "last_seen": iso(group["last_seen"]),
                    "window": [iso(args.start), iso(args.end)],
                },
            )
            for group in top
        ]
        evidence += [
            Evidence(
                evidence_id=record.log_id,
                kind="log_sample",
                service=record.service,
                timestamp=record.ts,
                summary=f"{iso(record.ts)} {record.level} {record.service}: {clip(record.message, 160)}",
                source_tool="query_logs",
                attributes={"message": record.message, "level": record.level},
            )
            for record in samples
        ]
        warnings: list[str] = []
        if result.partial:
            warnings.append("Partial results: some time ranges are missing (see missing_ranges).")
        if result.truncated:
            warnings.append(
                "Results were truncated by the backend; narrow the window or add filters."
            )
        if not result.records:
            warnings.append("No log lines matched.")
        if len(groups) > MAX_PATTERNS:
            warnings.append(
                f"Showing the {MAX_PATTERNS} most frequent of {len(groups)} patterns; filter with level or contains."
            )
        data = {
            "service": args.service,
            "window": {"start": iso(args.start), "end": iso(args.end)},
            "total_matches": len(result.records),
            "patterns_total": len(groups),
            "patterns": [
                {
                    "pattern_id": group["pattern_id"],
                    "level": group["level"],
                    "template": group["template"],
                    "count": group["count"],
                    "first_seen": iso(group["first"].ts),
                    "last_seen": iso(group["last_seen"]),
                }
                for group in top
            ],
            "samples": [
                {"log_id": r.log_id, "ts": iso(r.ts), "level": r.level, "message": r.message}
                for r in samples
            ],
            "missing_ranges": [[iso(a), iso(b)] for a, b in result.missing_ranges],
            "truncated": result.truncated,
        }
        return ToolOutput(
            data=data,
            evidence=evidence,
            partial=result.partial or result.truncated,
            warnings=warnings,
        )

    return [query_logs]
