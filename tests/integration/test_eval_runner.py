import json
from pathlib import Path

from investigator.evaluation.runner import run_eval
from investigator.settings import Settings
from tests.fakes import standard_llm


async def test_runner_concurrency_writes_valid_jsonl_and_resumes(
    test_settings: Settings, tmp_path: Path
) -> None:
    llm = standard_llm()
    kwargs = {
        "scenario_ids": ["INC-01", "INC-10"],
        "systems": ["multi", "single"],
        "trials": 1,
        "settings": test_settings,
        "out_dir": tmp_path,
        "concurrency": 2,
        "llm_factory": lambda: llm,
    }
    rows = await run_eval(**kwargs)  # type: ignore[arg-type]
    assert len(rows) == 4
    lines = (tmp_path / "results.jsonl").read_text().splitlines()
    parsed = [json.loads(line) for line in lines]
    assert sorted(row["key"] for row in parsed) == [
        "INC-01|multi|1",
        "INC-01|single|1",
        "INC-10|multi|1",
        "INC-10|single|1",
    ]
    first = next(row for row in parsed if row["key"] == "INC-01|multi|1")
    assert first["status"] == "ok" and first["score"]["rc_correct"] == 1.0
    assert first["metrics"]["llm_calls"] > 0 and first["cost_usd"] is None
    assert (tmp_path / "runs" / "INC-01" / "multi" / "trial-1" / "report.md").exists()
    assert json.loads((tmp_path / "config.json").read_text())["model"] == "scripted"
    again = await run_eval(**kwargs)  # type: ignore[arg-type]
    assert again == [] and len((tmp_path / "results.jsonl").read_text().splitlines()) == 4


async def test_runner_records_harness_errors_separately(
    test_settings: Settings, tmp_path: Path
) -> None:
    rows = await run_eval(
        scenario_ids=["INC-99"],
        systems=["multi"],
        trials=1,
        settings=test_settings,
        out_dir=tmp_path,
        concurrency=1,
        llm_factory=standard_llm,
    )
    assert rows == []
    errors = [json.loads(line) for line in (tmp_path / "errors.jsonl").read_text().splitlines()]
    assert errors[0]["error_class"] == "harness_error" and "INC-99" in errors[0]["error"]
    assert not (tmp_path / "results.jsonl").exists()
