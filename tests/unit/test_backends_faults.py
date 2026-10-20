from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from investigator.backends.base import (
    ChangeQuery,
    Deployment,
    DeploymentList,
    LogQuery,
    LogRecord,
    LogSearchResult,
)
from investigator.backends.faults import (
    FaultInjector,
    FaultRule,
    load_fault_rules,
    wrap_with_faults,
)
from investigator.backends.simulated import build_simulated_backends
from investigator.callctx import CURRENT_CALLER
from investigator.errors import OperationTimeout, RateLimited, ServiceUnavailable

T0 = datetime(2026, 3, 10, 10, 0, tzinfo=UTC)


def _logs(count: int) -> LogSearchResult:
    return LogSearchResult(
        records=[
            LogRecord(
                log_id=f"log-{i}",
                ts=T0 + timedelta(minutes=i),
                service="s",
                level="INFO",
                message="m",
            )
            for i in range(count)
        ]
    )


def _deployments(count: int) -> DeploymentList:
    return DeploymentList(
        items=[
            Deployment(
                deployment_id=f"dep-{i}",
                service="s",
                version="1",
                previous_version="0",
                started_at=T0 + timedelta(minutes=i),
                finished_at=T0 + timedelta(minutes=i + 1),
                status="succeeded",
                author="a",
                summary="x",
            )
            for i in range(count)
        ]
    )


def test_error_faults_count_per_caller_and_match_fields() -> None:
    injector = FaultInjector(
        [FaultRule(operation="search_logs", fault="timeout", match={"service": "a"}, first_n=2)]
    )
    assert injector.before("search_logs", {"service": "b"}) == []
    for _ in range(2):
        with pytest.raises(OperationTimeout):
            injector.before("search_logs", {"service": "a"})
    assert injector.before("search_logs", {"service": "a"}) == []
    token = CURRENT_CALLER.set("other-task")
    try:
        with pytest.raises(OperationTimeout):
            injector.before("search_logs", {"service": "a"})
    finally:
        CURRENT_CALLER.reset(token)
    assert len(injector.fired) == 3


def test_unavailable_and_rate_limited() -> None:
    injector = FaultInjector(
        [
            FaultRule(operation="list_deployments", fault="unavailable"),
            FaultRule(operation="get_metric", fault="rate_limited", params={"retry_after_s": 0.5}),
        ]
    )
    with pytest.raises(ServiceUnavailable):
        injector.before("list_deployments", {"service": None})
    with pytest.raises(RateLimited) as caught:
        injector.before("get_metric", {"service": "x", "metric": "y"})
    assert caught.value.retry_after_s == 0.5


def test_partial_drops_overlapping_ranges_only() -> None:
    rule = FaultRule(
        operation="search_logs",
        fault="partial",
        params={"drop_ranges": [["2026-03-10T10:02:00Z", "2026-03-10T10:05:00Z"]]},
    )
    injector = FaultInjector([rule])
    rules = injector.before("search_logs", {"service": "s"})
    result = injector.apply(rules, _logs(10), window=(T0, T0 + timedelta(minutes=10)))
    assert [r.log_id for r in result.records] == [
        "log-0",
        "log-1",
        "log-5",
        "log-6",
        "log-7",
        "log-8",
        "log-9",
    ]
    assert result.partial and len(result.missing_ranges) == 1
    untouched = injector.apply(rules, _logs(2), window=(T0, T0 + timedelta(minutes=1)))
    assert untouched.partial is False and len(untouched.records) == 2


def test_truncated_and_stale() -> None:
    truncate = FaultRule(operation="search_logs", fault="truncated", params={"max_items": 3})
    injector = FaultInjector([truncate])
    result = injector.apply([truncate], _logs(10))
    assert len(result.records) == 3 and result.truncated
    stale = FaultRule(
        operation="list_deployments", fault="stale", params={"hide_after": "2026-03-10T10:03:00Z"}
    )
    listing = FaultInjector([stale]).apply([stale], _deployments(6))
    assert [d.deployment_id for d in listing.items] == ["dep-0", "dep-1", "dep-2"]
    assert listing.stale_as_of == T0 + timedelta(minutes=3)


async def test_wrapped_backends_apply_scenario_faults(generated_root: Path) -> None:
    root = generated_root / "INC-07"
    rules = load_fault_rules(root / "environment" / "faults.json")
    assert [(r.operation, r.fault) for r in rules] == [("search_logs", "unavailable")]
    backends = wrap_with_faults(build_simulated_backends(root / "telemetry"), FaultInjector(rules))
    start = datetime(2026, 7, 8, 10, 0, tzinfo=UTC)
    window = {"start": start, "end": start + timedelta(hours=2)}
    with pytest.raises(ServiceUnavailable):
        await backends.logs.search_logs(LogQuery(service="risk-service", **window))
    orders = await backends.logs.search_logs(LogQuery(service="orders-service", **window))
    assert orders.records
    deployments = await backends.changes.list_deployments(ChangeQuery(**window))
    assert deployments.items
    assert load_fault_rules(root / "missing.json") == []
