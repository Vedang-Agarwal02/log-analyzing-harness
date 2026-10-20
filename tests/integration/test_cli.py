import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from investigator.cli import app
from investigator.harness.recording import RecordingLLM
from investigator.runtime import run_investigation
from investigator.settings import Settings
from tests.fakes import standard_llm

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "generate-data",
        "list-scenarios",
        "investigate",
        "show-trace",
        "eval",
        "summarize",
        "record-cassettes",
    ):
        assert command in result.output


def test_list_scenarios_uses_config(test_settings: Settings, tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(f"datasets_dir: {test_settings.datasets_dir}\n", encoding="utf-8")
    result = runner.invoke(app, ["list-scenarios", "--config", str(config)])
    assert result.exit_code == 0 and "INC-01" in result.output and "INC-12" in result.output


async def test_show_trace_prints_tree(test_settings: Settings, tmp_path: Path) -> None:
    await run_investigation(
        scenario_id="INC-01",
        system="multi",
        settings=test_settings,
        llm=standard_llm(),
        run_dir=tmp_path,
    )
    result = runner.invoke(app, ["show-trace", str(tmp_path)])
    assert result.exit_code == 0
    assert "gathering" in result.output and "tool query_metrics" in result.output


def test_investigate_replays_a_cassette(test_settings: Settings, tmp_path: Path) -> None:
    # Synchronous on purpose: the CLI calls asyncio.run(), which cannot run inside a running event loop.
    cassette = tmp_path / "INC-01-multi.json"
    recorder = RecordingLLM(cassette, mode="record", inner=standard_llm())
    asyncio.run(
        run_investigation(
            scenario_id="INC-01", system="multi", settings=test_settings, llm=recorder, run_dir=None
        )
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "llm: {model: scripted}\n"
        f"datasets_dir: {test_settings.datasets_dir}\n"
        f"runs_dir: {tmp_path / 'runs'}\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["investigate", "--scenario", "INC-01", "--replay", str(cassette), "--config", str(config)],
    )
    assert result.exit_code == 0, result.output
    assert "root_cause_identified" in result.output
    reports = list((tmp_path / "runs").glob("*/report.json"))
    assert (
        len(reports) == 1
        and json.loads(reports[0].read_text())["status"] == "root_cause_identified"
    )


def test_investigate_rejects_unknown_system() -> None:
    result = runner.invoke(app, ["investigate", "--system", "swarm"])
    assert result.exit_code != 0


@pytest.mark.parametrize("command", [["summarize", "/nonexistent/results"]])
def test_summarize_missing_directory_fails(command: list[str]) -> None:
    assert runner.invoke(app, command).exit_code != 0
