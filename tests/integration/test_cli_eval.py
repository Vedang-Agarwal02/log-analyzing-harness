from pathlib import Path

import pytest
from typer.testing import CliRunner

import investigator.cli as cli
from investigator.settings import Settings
from tests.fakes import standard_llm


def _config(test_settings: Settings, tmp_path: Path) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(
        "llm: {model: scripted}\n"
        f"datasets_dir: {test_settings.datasets_dir}\n"
        "simulation: {latency_ms_min: 0, latency_ms_max: 0}\n",
        encoding="utf-8",
    )
    return config


def test_eval_command_writes_results_and_applies_quality_gate(
    test_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "build_llm", lambda settings, **kwargs: standard_llm())
    runner = CliRunner()
    out = tmp_path / "results"
    args = [
        "eval",
        "--scenarios",
        "INC-01",
        "--systems",
        "multi",
        "--trials",
        "1",
        "--yes",
        "--out",
        str(out),
        "--config",
        str(_config(test_settings, tmp_path)),
    ]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert (out / "results.md").exists() and (out / "summary.json").exists()
    gated = runner.invoke(cli.app, [*args, "--min-accuracy", "1.5"])
    assert gated.exit_code == 2
    summarized = runner.invoke(cli.app, ["summarize", str(out)])
    # Rich wraps long paths at 80 columns, so compare without line breaks.
    assert summarized.exit_code == 0 and "results.md" in summarized.output.replace("\n", "")
