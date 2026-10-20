import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from investigator.cli import app
from investigator.harness.recording import RecordingLLM
from investigator.runtime import run_investigation
from investigator.settings import Settings
from tests.fakes import standard_llm

runner = CliRunner()


def test_investigate_accepts_a_run_id(test_settings: Settings, tmp_path: Path) -> None:
    cassette = tmp_path / "INC-01-multi.json"
    recorder = RecordingLLM(cassette, mode="record", inner=standard_llm())
    asyncio.run(
        run_investigation(
            scenario_id="INC-01", system="multi", settings=test_settings, llm=recorder, run_dir=None
        )
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        f"llm: {{model: scripted}}\ndatasets_dir: {test_settings.datasets_dir}\nruns_dir: {tmp_path / 'runs'}\n",
        encoding="utf-8",
    )
    args = [
        "investigate",
        "--scenario",
        "INC-01",
        "--replay",
        str(cassette),
        "--config",
        str(config),
        "--run-id",
        "demo-1",
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert (tmp_path / "runs" / "demo-1" / "report.md").exists()


def test_ui_command_starts_streamlit_on_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_call(command: list[str], **kwargs: Any) -> int:
        calls.append(command)
        return 0

    monkeypatch.setattr(subprocess, "call", fake_call)
    result = runner.invoke(app, ["ui", "--port", "8600"])
    assert result.exit_code == 0, result.output
    [command] = calls
    assert command[1:4] == ["-m", "streamlit", "run"]
    assert Path(command[4]).parts[-3:] == ("investigator", "ui", "app.py")
    assert command[-2:] == ["--server.port", "8600"]
