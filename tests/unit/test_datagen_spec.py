from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from investigator.datagen.spec import load_all_scenarios, load_scenario, parse_offset

MINIMAL = """
id: INC-99
slug: tiny
seed: 7
t0: "2026-03-10T10:12:00Z"
incident:
  title: "Something broke"
  severity: SEV3
  reporter: "tester"
  summary: "It broke."
  affected_services: [orders-service]
  customer_impact: "some"
metric_signals:
  - {service: orders-service, metric: error_rate_pct, shape: step, at: "-1m", to: 30}
ground_truth:
  expected_status: conclusive
  services: [orders-service]
  categories: [bad_deployment]
"""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("-4m", timedelta(minutes=-4)),
        ("+2h", timedelta(hours=2)),
        ("-2d3h", -timedelta(days=2, hours=3)),
        ("49h30m", timedelta(hours=49, minutes=30)),
        ("0m", timedelta(0)),
    ],
)
def test_parse_offset_valid(text: str, expected: timedelta) -> None:
    assert parse_offset(text) == expected


@pytest.mark.parametrize("text", ["", "-", "4x", "m", "1.5h"])
def test_parse_offset_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        parse_offset(text)


def test_load_scenario_applies_defaults(tmp_path: Path) -> None:
    path = tmp_path / "INC-99.yaml"
    path.write_text(MINIMAL, encoding="utf-8")
    spec = load_scenario(path)
    assert spec.window.before == "6h" and spec.window.after == "40m"
    assert spec.reported_offset == "+10m"
    assert spec.metrics_step_seconds == 60
    assert spec.changes == [] and spec.faults == []
    assert spec.metric_signals[0].to == 30
    assert spec.ground_truth.trigger is None
    assert [s.id for s in load_all_scenarios(tmp_path)] == ["INC-99"]


def test_scenario_rejects_unknown_shape(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(MINIMAL.replace("shape: step", "shape: wobble"), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_scenario(path)
