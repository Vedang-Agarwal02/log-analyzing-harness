"""Read-only helpers behind the demo UI. Everything here works on files the harness already writes.

This module does not import Streamlit, so it is unit-tested like the rest of the code.
"""

import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

from investigator.domain.common import LOG_LEVELS, iso, parse_iso
from investigator.domain.incident import IncidentReport

MAX_ROWS = 2000


@dataclass(frozen=True)
class RunInfo:
    path: Path
    run_id: str
    scenario: str
    system: str
    status: str
    termination: str
    model: str
    llm_calls: int
    tool_calls: int
    problems: int
    wall_s: float
    finished: bool

    @property
    def label(self) -> str:
        state = self.status if self.finished else "running"
        return f"{self.run_id} | {self.system} | {state}"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Rows of a JSONL file; unreadable lines (e.g. one being written right now) are skipped."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def problems(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Everything that went wrong in a run, in order: tool errors, LLM errors, failed tasks, fallbacks, crashes."""
    start = float(events[0].get("ts", 0.0)) if events else 0.0
    rows: list[dict[str, Any]] = []
    for event in events:
        kind = event.get("type")
        row = {
            "t_s": round(float(event.get("ts", start)) - start, 1),
            "task": event.get("task_id") or "",
        }
        if kind == "tool_call" and not event.get("ok"):
            detail = f"{event.get('error_type')} after {event.get('attempts')} attempt(s)"
            rows.append(
                row
                | {
                    "kind": "tool error",
                    "where": f"{event.get('agent')} -> {event.get('tool')}",
                    "detail": detail,
                }
            )
        elif kind == "llm_error":
            rows.append(
                row
                | {
                    "kind": "LLM error",
                    "where": str(event.get("agent")),
                    "detail": str(event.get("error")),
                }
            )
        elif kind == "llm_call" and event.get("fallback_used"):
            detail = f"answered by {event.get('served_model')}"
            rows.append(
                row | {"kind": "model fallback", "where": str(event.get("agent")), "detail": detail}
            )
        elif kind == "task_end" and event.get("status") == "failed":
            rows.append(
                row
                | {
                    "kind": "task failed",
                    "where": str(event.get("task_id")),
                    "detail": str(event.get("error")),
                }
            )
        elif kind == "fallback":
            detail = str(event.get("error") or "deterministic fallback used")
            rows.append(
                row | {"kind": "fallback", "where": str(event.get("component")), "detail": detail}
            )
        elif kind == "error":
            last_line = str(event.get("error", "")).strip().splitlines()[-1:] or [""]
            rows.append(
                row
                | {
                    "kind": "caught crash",
                    "where": str(event.get("component")),
                    "detail": last_line[0],
                }
            )
    return rows


def _detail(event: dict[str, Any]) -> str:
    kind = event.get("type")
    if kind == "llm_call":
        usage = event.get("usage") or {}
        prompt = sum(
            int(usage.get(key, 0))
            for key in ("input_tokens", "cache_read_tokens", "cache_write_tokens")
        )
        calls = ", ".join(event.get("tool_calls") or []) or "no tool calls"
        return f"{event.get('served_model')} stop={event.get('stop_reason')} in={prompt} out={usage.get('output_tokens', 0)} -> {calls}"
    if kind == "tool_call":
        outcome = "ok" if event.get("ok") else f"{event.get('error_type')} x{event.get('attempts')}"
        return f"{event.get('tool')} {outcome} {json.dumps(event.get('arguments') or {}, sort_keys=True)}"[
            :300
        ]
    if kind == "phase":
        return f"{event.get('name')} (round {event.get('round')})"
    skip = {"ts", "type", "run_id", "agent", "task_id"}
    return json.dumps(
        {key: value for key, value in event.items() if key not in skip}, sort_keys=True, default=str
    )[:300]


def timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start = float(events[0].get("ts", 0.0)) if events else 0.0
    return [
        {
            "t_s": round(float(event.get("ts", start)) - start, 1),
            "type": str(event.get("type")),
            "agent": str(event.get("agent") or event.get("component") or ""),
            "task": str(event.get("task_id") or ""),
            "detail": _detail(event),
        }
        for event in events
    ]


def run_info(path: Path, root: Path) -> RunInfo:
    events = read_jsonl(path / "trace.jsonl")
    start = next((event for event in events if event.get("type") == "run_start"), {})
    report = read_json(path / "report.json") or {}
    metrics = read_json(path / "metrics.json") or {}
    system = str(start.get("system") or report.get("system") or "?").removesuffix("_agent")
    return RunInfo(
        path=path,
        run_id=str(path.relative_to(root)),
        scenario=str(start.get("incident_id") or report.get("incident_id") or "?"),
        system=system,
        status=str(report.get("status") or "no report"),
        termination=str(report.get("termination_reason") or ""),
        model=str(start.get("model") or "?"),
        llm_calls=int(
            metrics.get("llm_calls")
            or sum(1 for event in events if event.get("type") == "llm_call")
        ),
        tool_calls=int(
            metrics.get("tool_calls")
            or sum(1 for event in events if event.get("type") == "tool_call")
        ),
        problems=len(problems(events)),
        wall_s=float(metrics.get("wall_s") or 0.0),
        finished=(path / "report.json").exists(),
    )


def find_runs(roots: list[Path]) -> list[RunInfo]:
    """Every directory under the roots that holds a trace.jsonl, newest first."""
    runs: list[RunInfo] = []
    for root in roots:
        if not root.exists():
            continue
        traces = sorted(
            root.rglob("trace.jsonl"), key=lambda trace: trace.stat().st_mtime, reverse=True
        )
        runs.extend(run_info(trace.parent, root) for trace in traces)
    return runs


def find_evaluations(root: Path) -> list[Path]:
    if not root.exists():
        return []
    found = [
        path
        for path in root.iterdir()
        if (path / "results.jsonl").exists() or (path / "results.md").exists()
    ]
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)


def run_files(run_dir: Path) -> list[Path]:
    return sorted(path for path in run_dir.iterdir() if path.is_file())


def file_kind(path: Path) -> str:
    return {".md": "markdown", ".json": "json", ".jsonl": "jsonl"}.get(path.suffix, "text")


def flatten(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Table-friendly rows: nested values become compact JSON strings."""
    flat: list[dict[str, str]] = []
    for row in rows[:MAX_ROWS]:
        flat.append(
            {
                key: value
                if isinstance(value, str)
                else json.dumps(value, sort_keys=True, default=str)
                for key, value in row.items()
            }
        )
    return flat


def parse_log_lines(text: str, services: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse lines of the form `TIMESTAMP LEVEL SERVICE message`, e.g. `2026-03-10T10:05:00Z ERROR orders-service Boom`."""
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=3)
        if len(parts) < 4:
            errors.append(f"line {number}: expected 'TIMESTAMP LEVEL SERVICE message'")
            continue
        stamp, level, service, message = parts
        try:
            ts = parse_iso(stamp)
        except ValueError:
            errors.append(
                f"line {number}: bad timestamp {stamp!r}; use ISO 8601 such as 2026-03-10T10:05:00Z"
            )
            continue
        if level.upper() not in LOG_LEVELS:
            errors.append(f"line {number}: level must be one of {', '.join(LOG_LEVELS)}")
            continue
        if service not in services:
            errors.append(f"line {number}: unknown service {service!r}")
            continue
        digest = hashlib.sha1(f"custom|{number}|{line}".encode()).hexdigest()[:10]
        records.append(
            {
                "log_id": f"log-{digest}",
                "ts": iso(ts),
                "service": service,
                "level": level.upper(),
                "message": message,
            }
        )
    return records, errors


def telemetry_window(scenario_root: Path) -> tuple[str, str]:
    """First and last timestamp covered by a scenario's telemetry; custom log lines must fall inside."""
    metrics = read_json(scenario_root / "telemetry" / "metrics.json") or {}
    start = parse_iso(str(metrics["start"]))
    end = start + timedelta(seconds=int(metrics["step_seconds"]) * int(metrics["points"]))
    return iso(start), iso(end)


def write_custom_config(path: Path, base_config: Path, datasets_dir: Path) -> Path:
    """A copy of the normal config whose datasets_dir points at the custom scenario folder."""
    data = (
        dict(yaml.safe_load(base_config.read_text(encoding="utf-8")) or {})
        if base_config.exists()
        else {}
    )
    data["datasets_dir"] = str(datasets_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


def make_custom_scenario(
    base_root: Path,
    out_root: Path,
    incident: IncidentReport,
    extra_logs: list[dict[str, Any]],
    *,
    keep_faults: bool,
) -> Path:
    """Build a scenario folder from a base scenario's telemetry plus your incident text and extra log lines.

    It has no ground_truth.json, so custom runs are never scored.
    """
    target = out_root / incident.incident_id
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(base_root / "telemetry", target / "telemetry")
    (target / "environment").mkdir(parents=True)
    faults = base_root / "environment" / "faults.json"
    fault_text = (
        faults.read_text(encoding="utf-8")
        if keep_faults and faults.exists()
        else '{"faults": []}\n'
    )
    (target / "environment" / "faults.json").write_text(fault_text, encoding="utf-8")
    (target / "incident.json").write_text(incident.model_dump_json(indent=1), encoding="utf-8")
    if extra_logs:
        with (target / "telemetry" / "logs.jsonl").open("a", encoding="utf-8") as handle:
            for record in extra_logs:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
    return target


def investigate_command(
    *,
    scenario: str,
    system: str,
    run_id: str,
    model: str = "",
    config: Path | None = None,
    replay: Path | None = None,
) -> list[str]:
    """The exact CLI call the UI runs in the background, so a demo uses the same harness as the terminal."""
    command = [
        sys.executable,
        "-m",
        "investigator.cli",
        "investigate",
        "--scenario",
        scenario,
        "--system",
        system,
        "--run-id",
        run_id,
    ]
    if model:
        command += ["--model", model]
    if config is not None:
        command += ["--config", str(config)]
    if replay is not None:
        command += ["--replay", str(replay)]
    return command
