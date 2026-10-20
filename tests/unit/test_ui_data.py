import asyncio
import json
from pathlib import Path

from investigator.backends.simulated import SimulatedDataset
from investigator.domain.common import iso
from investigator.domain.incident import IncidentReport
from investigator.runtime import load_incident, run_investigation
from investigator.settings import Settings, load_settings
from investigator.ui import data
from tests.fakes import observability, standard_llm


def _failing_run(settings: Settings, root: Path) -> Path:
    """A scripted INC-07 run whose logs backend is down, so the trace contains failed tool calls."""
    run_dir = root / "runs" / "demo-run"
    llm = standard_llm(observability=observability(service="risk-service"))
    asyncio.run(
        run_investigation(
            scenario_id="INC-07",
            system="multi",
            settings=settings,
            llm=llm,
            run_dir=run_dir,
            run_id="demo-run",
        )
    )
    return run_dir


def test_find_runs_reads_status_counts_problems_and_files(
    test_settings: Settings, tmp_path: Path
) -> None:
    run_dir = _failing_run(test_settings, tmp_path)
    [run] = data.find_runs([tmp_path / "runs", tmp_path / "missing"])
    assert (run.run_id, run.scenario, run.system, run.finished) == (
        "demo-run",
        "INC-07",
        "multi",
        True,
    )
    assert run.llm_calls > 0 and run.tool_calls > 0 and run.problems >= 1
    rows = data.problems(data.read_jsonl(run_dir / "trace.jsonl"))
    assert any(
        row["kind"] == "tool error"
        and "query_logs" in row["where"]
        and "unavailable" in row["detail"]
        for row in rows
    )
    names = {path.name for path in data.run_files(run_dir)}
    assert {
        "report.md",
        "report.json",
        "trace.jsonl",
        "metrics.json",
        "evidence.json",
        "state.json",
    } <= names


def test_timeline_and_problems_describe_each_event() -> None:
    events = [
        {"ts": 10.0, "type": "phase", "name": "planning", "round": 1, "run_id": "r"},
        {
            "ts": 11.5,
            "type": "tool_call",
            "agent": "observability",
            "task_id": "R1-T1",
            "tool": "query_logs",
            "ok": False,
            "error_type": "timeout",
            "attempts": 3,
            "arguments": {"service": "a"},
        },
        {"ts": 12.0, "type": "fallback", "component": "reporter"},
    ]
    rows = data.timeline(events)
    assert rows[0] == {
        "t_s": 0.0,
        "type": "phase",
        "agent": "",
        "task": "",
        "detail": "planning (round 1)",
    }
    assert rows[1]["t_s"] == 1.5 and rows[1]["detail"].startswith("query_logs timeout x3")
    assert [row["kind"] for row in data.problems(events)] == ["tool error", "fallback"]
    assert data.flatten([{"a": {"b": 1}, "c": "x"}]) == [{"a": '{"b": 1}', "c": "x"}]
    assert [data.file_kind(Path(name)) for name in ("r.md", "r.json", "t.jsonl", "x.log")] == [
        "markdown",
        "json",
        "jsonl",
        "text",
    ]


def test_parse_log_lines_accepts_valid_lines_and_explains_bad_ones() -> None:
    text = "\n".join(
        [
            "2026-03-10T10:05:00Z error orders-service Payment call timed out after 30s",
            "",
            "# a comment",
            "too short",
            "yesterday INFO orders-service x",
            "2026-03-10T10:06:00Z TRACE orders-service x",
            "2026-03-10T10:07:00Z INFO nope-service x",
        ]
    )
    records, errors = data.parse_log_lines(text, {"orders-service"})
    assert len(records) == 1 and records[0]["log_id"].startswith("log-")
    assert {key: records[0][key] for key in ("ts", "service", "level", "message")} == {
        "ts": "2026-03-10T10:05:00Z",
        "service": "orders-service",
        "level": "ERROR",
        "message": "Payment call timed out after 30s",
    }
    assert [error.split(":")[0] for error in errors] == ["line 4", "line 5", "line 6", "line 7"]


def test_custom_scenario_is_a_loadable_unscored_dataset(
    generated_root: Path, tmp_path: Path
) -> None:
    base = generated_root / "INC-01"
    original = load_incident(base)
    incident = IncidentReport.model_validate(
        {**original.model_dump(), "incident_id": "CUSTOM-1", "title": "Checkout is slow"}
    )
    logs, errors = data.parse_log_lines(
        f"{iso(original.reported_at)} ERROR orders-service Custom demo failure", {"orders-service"}
    )
    root = data.make_custom_scenario(base, tmp_path / "datasets", incident, logs, keep_faults=False)
    assert root == tmp_path / "datasets" / "CUSTOM-1" and errors == []
    assert load_incident(root).title == "Checkout is slow"
    assert not (root / "ground_truth.json").exists()
    assert json.loads((root / "environment" / "faults.json").read_text()) == {"faults": []}
    dataset = SimulatedDataset.load(root / "telemetry")
    assert any(record.message == "Custom demo failure" for record in dataset.logs["orders-service"])
    start, end = data.telemetry_window(root)
    assert start < iso(original.reported_at) < end


def test_custom_config_and_cli_command(repo_root: Path, tmp_path: Path) -> None:
    config = data.write_custom_config(
        tmp_path / "config.yaml", repo_root / "config" / "default.yaml", tmp_path / "datasets"
    )
    loaded = load_settings(config)
    assert (
        loaded.datasets_dir == str(tmp_path / "datasets") and loaded.llm.model == "claude-opus-5-5"
    )
    command = data.investigate_command(
        scenario="INC-02", system="single", run_id="r1", model="gemini-4-argon", config=config
    )
    assert command[1:4] == ["-m", "investigator.cli", "investigate"]
    assert (
        command[command.index("--run-id") + 1] == "r1"
        and command[command.index("--model") + 1] == "gemini-4-argon"
    )
    assert "--replay" not in data.investigate_command(
        scenario="INC-01", system="multi", run_id="r2"
    )
