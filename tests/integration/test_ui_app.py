"""Headless tests of the Streamlit demo UI (no browser, no real background runs)."""

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from investigator.domain.common import iso
from investigator.runtime import load_incident, run_investigation
from investigator.settings import Settings
from tests.fakes import observability, standard_llm

APP = Path(__file__).resolve().parents[2] / "src" / "investigator" / "ui" / "app.py"


class FakeProcess:
    def poll(self) -> int | None:
        return None


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    commands: list[list[str]] = []

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        commands.append(command)
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return commands


def _app(test_settings: Settings, tmp_path: Path) -> AppTest:
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.session_state["runs_root"] = str(tmp_path / "runs")
    app.session_state["evals_root"] = str(tmp_path / "evals")
    app.session_state["datasets_dir"] = test_settings.datasets_dir
    return app


def test_runs_and_evaluations_tabs_show_files_and_problems(
    test_settings: Settings, tmp_path: Path
) -> None:
    llm = standard_llm(observability=observability(service="risk-service"))
    asyncio.run(
        run_investigation(
            scenario_id="INC-07",
            system="multi",
            settings=test_settings,
            llm=llm,
            run_dir=tmp_path / "runs" / "demo-run",
            run_id="demo-run",
        )
    )
    evaluation = tmp_path / "evals" / "final-demo"
    evaluation.mkdir(parents=True)
    (evaluation / "results.md").write_text("# Evaluation results\n\nDemo table\n", encoding="utf-8")
    (evaluation / "errors.jsonl").write_text(
        json.dumps({"key": "INC-01|multi|1", "error_class": "timeout"}) + "\n", encoding="utf-8"
    )
    app = _app(test_settings, tmp_path).run()
    assert not app.exception
    assert app.selectbox(key="run_choice").value == "demo-run"
    assert any(tab.label.startswith("Problems (") for tab in app.tabs)
    kinds = [
        kind
        for frame in app.dataframe
        if "kind" in frame.value.columns
        for kind in frame.value["kind"].tolist()
    ]
    assert "tool error" in kinds
    assert any("Demo table" in block.value for block in app.markdown)
    assert any("Infrastructure errors (1)" in header.value for header in app.subheader)


def test_run_button_starts_the_cli_in_the_background(
    test_settings: Settings, tmp_path: Path, started: list[list[str]]
) -> None:
    app = _app(test_settings, tmp_path).run()
    app.button(key="run_builtin").click().run()
    assert not app.exception
    [command] = started
    assert command[1:4] == ["-m", "investigator.cli", "investigate"] and "--run-id" in command
    assert any(": running" in header.value for header in app.subheader)


def test_custom_incident_validates_logs_then_runs_with_its_own_config(
    test_settings: Settings, tmp_path: Path, started: list[list[str]]
) -> None:
    app = _app(test_settings, tmp_path).run()
    app.radio(key="source").set_value("Custom incident").run()
    app.text_area(key="logs").set_value("oops").run()
    app.button(key="run_custom").click().run()
    assert not app.exception and started == []
    assert any("expected 'TIMESTAMP LEVEL SERVICE message'" in error.value for error in app.error)
    reported = iso(load_incident(Path(test_settings.datasets_dir) / "INC-01").reported_at)
    app.text_area(key="logs").set_value(
        f"{reported} ERROR orders-service Demo failure typed by hand"
    ).run()
    app.button(key="run_custom").click().run()
    assert not app.exception
    [command] = started
    config = Path(command[command.index("--config") + 1])
    scenario = command[command.index("--scenario") + 1]
    assert config.exists() and scenario.startswith("CUSTOM-")
    logs = (config.parent / "datasets" / scenario / "telemetry" / "logs.jsonl").read_text(
        encoding="utf-8"
    )
    assert "Demo failure typed by hand" in logs
