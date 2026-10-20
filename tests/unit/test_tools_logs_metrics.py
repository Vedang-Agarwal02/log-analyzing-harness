import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from investigator.backends.base import Backends
from investigator.backends.simulated import build_simulated_backends
from investigator.domain.common import iso, parse_iso
from investigator.domain.evidence import EvidenceStore
from investigator.harness.budget import Budget, BudgetLimits
from investigator.harness.executor import ToolExecutor
from investigator.harness.hooks import HookRunner
from investigator.harness.llm import ToolCall
from investigator.harness.tools import Tool, ToolContext, ToolRegistry
from investigator.jsonutil import canonical_json
from investigator.tools.logs import build_log_tools
from investigator.tools.metrics import build_metric_tools

T0 = parse_iso("2026-03-10T10:12:00Z")


@pytest.fixture
def backends(generated_root: Path) -> Backends:
    return build_simulated_backends(generated_root / "INC-01" / "telemetry")


@pytest.fixture
def tools(backends: Backends) -> dict[str, Tool]:
    return {t.name: t for t in [*build_log_tools(backends), *build_metric_tools(backends)]}


async def _run(tool: Tool, args: dict[str, Any], store: EvidenceStore | None = None):  # type: ignore[no-untyped-def]
    return await tool.run(
        tool.parse_arguments(args), ToolContext(caller="t", evidence=store or EvidenceStore())
    )


async def _envelope(tools: dict[str, Tool], name: str, args: dict[str, Any]) -> dict[str, Any]:
    executor = ToolExecutor(
        ToolRegistry(tools.values()), budget=Budget(BudgetLimits()), hooks=HookRunner()
    )
    part = await executor.execute(
        ToolCall("c1", name, args),
        ToolContext(caller="t", evidence=EvidenceStore()),
        agent="a",
        task_id=None,
    )
    return dict(json.loads(part.content))


def _window(before_min: int, after_min: int) -> dict[str, str]:
    return {
        "start": iso(T0 - timedelta(minutes=before_min)),
        "end": iso(T0 + timedelta(minutes=after_min)),
    }


async def test_query_logs_returns_patterns_samples_and_evidence(tools: dict[str, Tool]) -> None:
    output = await _run(
        tools["query_logs"], {"service": "orders-service", "level": "WARN", **_window(30, 20)}
    )
    patterns = output.data["patterns"]
    assert any("NullPointerException in LimitPriceValidator" in p["template"] for p in patterns)
    assert all(p["pattern_id"].startswith("logpat-") for p in patterns)
    assert len(output.data["samples"]) <= 8
    kinds = {e.kind for e in output.evidence}
    assert kinds == {"log_pattern", "log_sample"}
    assert output.data["total_matches"] >= sum(p["count"] for p in patterns)


async def test_query_logs_output_is_capped(tools: dict[str, Tool]) -> None:
    output = await _run(
        tools["query_logs"], {"service": "api-gateway", "max_samples": 30, **_window(360, 30)}
    )
    assert len(output.data["patterns"]) <= 15
    assert len(output.data["samples"]) <= 30


async def test_query_logs_unknown_service_returns_not_found(tools: dict[str, Tool]) -> None:
    envelope = await _envelope(
        tools, "query_logs", {"service": "Orders-Service ", **_window(10, 10)}
    )
    assert envelope["ok"] is False and envelope["error"]["type"] == "not_found"
    assert "list_services" in envelope["error"]["hint"]


async def test_query_logs_rejects_huge_window(tools: dict[str, Tool]) -> None:
    envelope = await _envelope(
        tools, "query_logs", {"service": "orders-service", **_window(60 * 24 * 5, 0)}
    )
    assert envelope["error"]["type"] == "invalid_arguments"


async def test_query_metrics_downsamples_to_60_points(tools: dict[str, Tool]) -> None:
    output = await _run(
        tools["query_metrics"],
        {"service": "orders-service", "metric": "error_rate_pct", **_window(360, 30)},
    )
    assert 0 < len(output.data["points"]) <= 60
    change = output.data["change_point"]
    assert change is not None and change["direction"] == "up"
    assert abs((parse_iso(change["at"]) - T0).total_seconds()) <= 300
    assert output.data["series_id"].startswith("met-")
    assert output.evidence[0].attributes["metric"] == "error_rate_pct"


async def test_list_metrics_and_external_warning(tools: dict[str, Tool]) -> None:
    orders = await _run(tools["list_metrics"], {"service": "orders-service"})
    assert "db_pool_wait_ms" in [m["metric"] for m in orders.data["metrics"]]
    external = await _run(tools["list_metrics"], {"service": "bank-gateway"})
    assert external.data["metrics"] == [] and external.warnings


async def test_detect_anomalies_ranks_change_points(tools: dict[str, Tool]) -> None:
    store = EvidenceStore()
    output = await _run(tools["detect_anomalies"], {"max_results": 10, **_window(120, 20)}, store)
    rows = output.data["anomalies"]
    assert rows and len(rows) <= 10
    assert [r["severity"] for r in rows] == sorted((r["severity"] for r in rows), reverse=True)
    assert ("orders-service", "error_rate_pct") in {(r["service"], r["metric"]) for r in rows}
    assert output.data["scanned_series"] > 50
    assert all(e.evidence_id.startswith("met-") for e in output.evidence)


async def test_tool_output_is_deterministic(tools: dict[str, Tool]) -> None:
    args = {"service": "orders-service", "level": "WARN", **_window(30, 20)}
    first = await _run(tools["query_logs"], args)
    second = await _run(tools["query_logs"], args)
    assert canonical_json(first.data) == canonical_json(second.data)
