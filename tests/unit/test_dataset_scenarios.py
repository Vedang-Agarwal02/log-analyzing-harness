"""Validates the 12 scenarios: coverage, reachable ground truth, no leakage, freshness."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

EXPECTED_IDS = [f"INC-{index:02d}" for index in range(1, 13)]


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _truth(root: Path, scenario_id: str) -> dict[str, Any]:
    return dict(_load(root / scenario_id / "ground_truth.json"))


def test_all_twelve_scenarios_generate(generated_root: Path) -> None:
    assert sorted(path.name for path in generated_root.iterdir()) == EXPECTED_IDS


def test_scenario_coverage_requirements(generated_root: Path) -> None:
    truths = [_truth(generated_root, sid) for sid in EXPECTED_IDS]
    tags = Counter(tag for truth in truths for tag in set(truth["tags"]))
    assert tags["misleading_evidence"] + tags["misleading_history"] >= 6
    assert tags["conflicting_observations"] >= 3
    assert tags["tool_failure"] >= 4
    assert tags["inconclusive"] == 1
    primary = {
        truth["categories"][0] for truth in truths if truth["expected_status"] == "conclusive"
    }
    assert len(primary) >= 6


@pytest.mark.parametrize("scenario_id", EXPECTED_IDS)
def test_ground_truth_is_reachable_in_telemetry(generated_root: Path, scenario_id: str) -> None:
    root = generated_root / scenario_id
    truth = _truth(generated_root, scenario_id)
    telemetry = root / "telemetry"
    metrics = _load(telemetry / "metrics.json")["series"]
    logs = [
        json.loads(line)
        for line in (telemetry / "logs.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    deployments = _load(telemetry / "deployments.json")["deployments"]
    configs = _load(telemetry / "config_changes.json")["config_changes"]
    change_ids = {d["deployment_id"] for d in deployments} | {c["change_id"] for c in configs}
    for key in truth["key_evidence"]:
        if key["kind"] == "change":
            assert key["id"] in change_ids
        elif key["kind"] == "metric_series":
            assert key["metric"] in metrics[key["service"]]
        else:
            needle = key["contains"].lower()
            assert any(
                r["service"] == key["service"] and needle in r["message"].lower() for r in logs
            ), key
    if truth["trigger_id"] is not None:
        assert truth["trigger_id"] in change_ids
    for change_id in truth["red_herring_ids"]:
        assert change_id in change_ids
    incident_text = (root / "incident.json").read_text(encoding="utf-8")
    assert _load(root / "incident.json")["incident_id"] == scenario_id
    for change_id in change_ids:
        assert change_id not in incident_text


def test_committed_dataset_is_fresh(repo_root: Path, generated_root: Path) -> None:
    committed = repo_root / "datasets" / "generated"
    if not (committed / "INC-01").exists():
        pytest.skip("datasets/generated has not been generated yet")
    for path in sorted(p for p in generated_root.rglob("*") if p.is_file()):
        other = committed / path.relative_to(generated_root)
        assert other.exists(), (
            f"missing {other}: run `uv run python -m investigator.datagen.generate`"
        )
        assert other.read_bytes() == path.read_bytes(), f"stale {other}: regenerate the dataset"
