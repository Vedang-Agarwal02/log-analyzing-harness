import json
import random
from pathlib import Path

import pytest

from investigator.datagen.generate import GenerationError, fill_template, generate_scenario
from investigator.datagen.platform import Platform, load_platform
from investigator.datagen.spec import ScenarioSpec

SPEC = {
    "id": "INC-T1",
    "slug": "unit",
    "seed": 11,
    "t0": "2026-03-10T10:12:00Z",
    "window": {"before": "2h", "after": "20m"},
    "incident": {
        "title": "Orders failing",
        "severity": "SEV2",
        "reporter": "unit test",
        "summary": "Orders fail.",
        "affected_services": ["orders-service"],
        "customer_impact": "some",
    },
    "changes": [
        {
            "key": "rel",
            "type": "deploy",
            "service": "orders-service",
            "at": "-4m",
            "version": "4.18.0",
            "previous_version": "4.17.3",
            "summary": "Refactor validation",
        }
    ],
    "metric_signals": [
        {
            "service": "orders-service",
            "metric": "error_rate_pct",
            "shape": "step",
            "at": "-1m",
            "to": 30,
        }
    ],
    "log_signals": [
        {
            "service": "orders-service",
            "level": "ERROR",
            "template": "Validation crashed for order {id}",
            "at": "-1m",
            "rate_per_min": 5,
        }
    ],
    "clock_skews": [{"service": "risk-service", "offset_seconds": -240}],
    "faults": [
        {
            "operation": "get_metric",
            "fault": "partial",
            "match": {"service": "orders-service"},
            "params": {"drop_ranges": [["-10m", "-5m"]]},
        }
    ],
    "ground_truth": {
        "expected_status": "conclusive",
        "services": ["orders-service"],
        "categories": ["bad_deployment"],
        "trigger": "rel",
        "key_evidence": [
            {"kind": "change", "change": "rel"},
            {"kind": "metric_series", "service": "orders-service", "metric": "error_rate_pct"},
            {"kind": "log_pattern", "service": "orders-service", "contains": "Validation crashed"},
        ],
        "tags": ["unit"],
    },
}


@pytest.fixture(scope="module")
def platform(repo_root: Path) -> Platform:
    return load_platform(repo_root / "datasets" / "platform.yaml")


def _read(root: Path, name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((root / name).read_text(encoding="utf-8"))


def test_fill_template_placeholders() -> None:
    text = fill_template("a={ms} b={n} c={id} d={choice:x|y} e={int:3-3}", random.Random(1))
    parts = dict(item.split("=") for item in text.split(" "))
    assert 5 <= int(parts["a"]) <= 900
    assert 1 <= int(parts["b"]) <= 99999
    assert len(parts["c"]) == 8
    assert parts["d"] in {"x", "y"}
    assert parts["e"] == "3"
    with pytest.raises(GenerationError):
        fill_template("{nope}", random.Random(1))


def test_generate_writes_layout_and_resolves_ground_truth(
    tmp_path: Path, platform: Platform
) -> None:
    root = generate_scenario(ScenarioSpec.model_validate(SPEC), platform, tmp_path)
    for name in [
        "incident.json",
        "ground_truth.json",
        "environment/faults.json",
        "telemetry/logs.jsonl",
        "telemetry/metrics.json",
        "telemetry/deployments.json",
        "telemetry/config_changes.json",
        "telemetry/topology.json",
        "telemetry/history.json",
    ]:
        assert (root / name).exists(), name
    truth = _read(root, "ground_truth.json")
    deployments = _read(root, "telemetry/deployments.json")["deployments"]
    trigger = next(d for d in deployments if d["deployment_id"] == truth["trigger_id"])
    assert trigger["service"] == "orders-service" and trigger["version"] == "4.18.0"
    assert truth["key_evidence"][0] == {"kind": "change", "id": truth["trigger_id"]}
    assert _read(root, "incident.json")["reported_at"] == "2026-03-10T10:22:00Z"
    fault = _read(root, "environment/faults.json")["faults"][0]
    assert fault["params"]["drop_ranges"] == [["2026-03-10T10:02:00Z", "2026-03-10T10:07:00Z"]]
    telemetry_text = "".join(p.read_text(encoding="utf-8") for p in (root / "telemetry").iterdir())
    for forbidden in ("ground_truth", "red_herring", "trigger_id", "key_evidence"):
        assert forbidden not in telemetry_text


def test_generation_is_deterministic(tmp_path: Path, platform: Platform) -> None:
    spec = ScenarioSpec.model_validate(SPEC)
    first = generate_scenario(spec, platform, tmp_path / "a")
    second = generate_scenario(spec, platform, tmp_path / "b")
    for path in sorted(p for p in first.rglob("*") if p.is_file()):
        other = second / path.relative_to(first)
        assert path.read_bytes() == other.read_bytes(), path.name


def test_metric_and_log_signals_are_applied(tmp_path: Path, platform: Platform) -> None:
    root = generate_scenario(ScenarioSpec.model_validate(SPEC), platform, tmp_path)
    metrics = _read(root, "telemetry/metrics.json")
    values = metrics["series"]["orders-service"]["error_rate_pct"]["values"]
    assert metrics["points"] == len(values) == 140
    onset_index = 120 - 1  # t0 - 1m with a 2h window at 60 s steps
    assert max(values[:onset_index]) < 1.0
    assert min(values[onset_index:]) > 12.0
    assert "bank-gateway" not in metrics["series"]
    rows = [json.loads(line) for line in (root / "telemetry/logs.jsonl").read_text().splitlines()]
    assert len({row["log_id"] for row in rows}) == len(rows)
    assert [row["ts"] for row in rows] == sorted(row["ts"] for row in rows)
    crashes = [row for row in rows if row["message"].startswith("Validation crashed")]
    assert len(crashes) >= 80
    assert min(row["ts"] for row in crashes) >= "2026-03-10T10:11:00Z"


def test_clock_skew_shifts_service_logs(tmp_path: Path, platform: Platform) -> None:
    skewed = generate_scenario(ScenarioSpec.model_validate(SPEC), platform, tmp_path / "s")
    unskewed_spec = dict(SPEC, clock_skews=[])
    plain = generate_scenario(ScenarioSpec.model_validate(unskewed_spec), platform, tmp_path / "p")

    def first_risk_ts(root: Path) -> str:
        for line in (root / "telemetry/logs.jsonl").read_text().splitlines():
            row = json.loads(line)
            if row["service"] == "risk-service":
                return str(row["ts"])
        raise AssertionError("no risk-service logs")

    assert first_risk_ts(skewed) < first_risk_ts(plain)


def test_invalid_scenario_lists_problems(tmp_path: Path, platform: Platform) -> None:
    bad = dict(SPEC)
    bad["metric_signals"] = [
        {
            "service": "orders-service",
            "metric": "no_such_metric",
            "shape": "step",
            "at": "0m",
            "to": 1,
        }
    ]
    with pytest.raises(GenerationError, match="no_such_metric"):
        generate_scenario(ScenarioSpec.model_validate(bad), platform, tmp_path)
