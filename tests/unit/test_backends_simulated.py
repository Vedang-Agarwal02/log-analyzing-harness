from datetime import timedelta
from pathlib import Path

import pytest

from investigator.backends.base import Backends, ChangeQuery, LogQuery, MetricQuery
from investigator.backends.simulated import build_simulated_backends, load_services
from investigator.domain.common import parse_iso
from investigator.errors import InvalidInput, NotFound

T0 = parse_iso("2026-03-10T10:12:00Z")


@pytest.fixture
def backends(generated_root: Path) -> Backends:
    return build_simulated_backends(generated_root / "INC-01" / "telemetry")


async def test_search_logs_filters(backends: Backends) -> None:
    query = LogQuery(
        service="orders-service",
        start=T0 - timedelta(minutes=30),
        end=T0 + timedelta(minutes=20),
        min_level="ERROR",
        contains="nullpointer",
    )
    result = await backends.logs.search_logs(query)
    assert result.records and all(r.level == "ERROR" for r in result.records)
    assert all("NullPointerException" in r.message for r in result.records)
    assert all(query.start <= r.ts < query.end for r in result.records)
    assert [r.ts for r in result.records] == sorted(r.ts for r in result.records)


async def test_search_logs_errors(backends: Backends) -> None:
    with pytest.raises(NotFound) as caught:
        await backends.logs.search_logs(
            LogQuery(service="ghost", start=T0, end=T0 + timedelta(minutes=1))
        )
    assert caught.value.hint is not None and "list_services" in caught.value.hint
    with pytest.raises(InvalidInput):
        await backends.logs.search_logs(LogQuery(service="orders-service", start=T0, end=T0))


async def test_get_metric_returns_window(backends: Backends) -> None:
    query = MetricQuery(
        service="orders-service",
        metric="error_rate_pct",
        start=T0 - timedelta(minutes=10),
        end=T0 + timedelta(minutes=10),
    )
    series = await backends.metrics.get_metric(query)
    assert len(series.points) == 20 and series.step_seconds == 60 and series.unit == "%"
    assert series.points[0][0] == T0 - timedelta(minutes=10)
    assert series.points[-1][1] > 15
    with pytest.raises(NotFound):
        await backends.metrics.get_metric(query.model_copy(update={"metric": "nope"}))
    descriptors = await backends.metrics.list_metrics("orders-service")
    assert [d.metric for d in descriptors] == sorted(d.metric for d in descriptors)
    assert await backends.metrics.list_metrics("bank-gateway") == []


async def test_changes_topology_and_history(backends: Backends, generated_root: Path) -> None:
    window = ChangeQuery(start=T0 - timedelta(days=8), end=T0 + timedelta(hours=1))
    deployments = await backends.changes.list_deployments(window)
    assert any(d.service == "orders-service" and d.version == "4.18.0" for d in deployments.items)
    only_orders = await backends.changes.list_deployments(
        window.model_copy(update={"service": "orders-service"})
    )
    assert {d.service for d in only_orders.items} == {"orders-service"}
    with pytest.raises(NotFound):
        await backends.changes.list_config_changes(window.model_copy(update={"service": "ghost"}))
    orders = await backends.topology.get_service("orders-service")
    assert "risk-service" in orders.depends_on
    assert len(await backends.topology.list_services()) == 21
    hits = await backends.history.search_incidents(
        "null pointer validation 500 after release", None, 3
    )
    assert hits and "null pointer" in hits[0].incident.title
    assert len(load_services(generated_root / "INC-01" / "telemetry")) == 21
