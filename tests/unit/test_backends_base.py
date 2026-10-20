from datetime import UTC, datetime

from investigator.backends.base import Deployment, DeploymentList, LogSearchResult, MetricSeries


def test_records_parse_iso_strings_and_round_trip() -> None:
    series = MetricSeries.model_validate(
        {
            "service": "orders-service",
            "metric": "error_rate_pct",
            "unit": "%",
            "step_seconds": 60,
            "points": [["2026-03-10T10:00:00Z", 0.3], ["2026-03-10T10:01:00Z", 0.4]],
        }
    )
    assert series.points[0] == (datetime(2026, 3, 10, 10, 0, tzinfo=UTC), 0.3)
    assert series.partial is False and series.missing_ranges == []
    clone = MetricSeries.model_validate_json(series.model_dump_json())
    assert clone == series


def test_list_results_have_flags() -> None:
    deployment = Deployment.model_validate(
        {
            "deployment_id": "dep-0001",
            "service": "orders-service",
            "version": "1.0.1",
            "previous_version": "1.0.0",
            "started_at": "2026-03-10T10:00:00Z",
            "finished_at": "2026-03-10T10:02:00Z",
            "status": "succeeded",
            "author": "a",
            "summary": "s",
        }
    )
    listing = DeploymentList(items=[deployment])
    assert listing.truncated is False and listing.stale_as_of is None
    assert LogSearchResult(records=[]).partial is False
