import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from investigator.backends.simulated import build_simulated_backends
from investigator.domain.common import iso, parse_iso
from investigator.domain.evidence import EvidenceStore
from investigator.harness.budget import Budget, BudgetLimits
from investigator.harness.executor import ToolExecutor
from investigator.harness.hooks import HookRunner
from investigator.harness.llm import ToolCall
from investigator.harness.tools import ToolContext, ToolRegistry
from investigator.tools.registry import DATA_TOOL_NAMES, build_registry

T0 = parse_iso("2026-03-10T10:12:00Z")


@pytest.fixture
def registry(generated_root: Path) -> ToolRegistry:
    return build_registry(build_simulated_backends(generated_root / "INC-01" / "telemetry"))


async def _call(
    registry: ToolRegistry, name: str, args: dict[str, Any], store: EvidenceStore
) -> dict[str, Any]:
    executor = ToolExecutor(registry, budget=Budget(BudgetLimits()), hooks=HookRunner())
    part = await executor.execute(
        ToolCall("c", name, args), ToolContext(caller="t", evidence=store), agent="a", task_id=None
    )
    return dict(json.loads(part.content))


def test_registry_has_all_tools_with_object_schemas(registry: ToolRegistry) -> None:
    assert registry.names() == sorted(DATA_TOOL_NAMES) and len(DATA_TOOL_NAMES) == 10
    for spec in registry.specs():
        assert spec.input_schema["type"] == "object" and "properties" in spec.input_schema
        assert len(spec.description) > 40


async def test_change_tools(registry: ToolRegistry) -> None:
    store = EvidenceStore()
    window = {"start": iso(T0 - timedelta(days=8)), "end": iso(T0 + timedelta(hours=1))}
    deployments = await _call(registry, "get_deployments", window, store)
    assert deployments["ok"] and deployments["data"]["count"] >= 1
    target = next(d for d in deployments["data"]["deployments"] if d["version"] == "4.18.0")
    stored = store.get(target["deployment_id"])
    assert (
        stored is not None and stored.kind == "deployment" and "4.17.3 -> 4.18.0" in stored.summary
    )
    configs = await _call(registry, "get_config_changes", window, store)
    assert configs["ok"] and all(e.startswith("cfg-") for e in configs["evidence_ids"])
    too_wide = await _call(
        registry, "get_deployments", {"start": iso(T0 - timedelta(days=20)), "end": iso(T0)}, store
    )
    assert too_wide["error"]["type"] == "invalid_arguments"


async def test_topology_history_and_evidence_tools(registry: ToolRegistry) -> None:
    store = EvidenceStore()
    deps = await _call(registry, "get_service_dependencies", {"service": "orders-service"}, store)
    assert "api-gateway" in [d["service"] for d in deps["data"]["dependents"]]
    assert deps["evidence_ids"] == ["topo-orders-service"]
    services = await _call(registry, "list_services", {}, store)
    assert len(services["data"]["services"]) == 21 and services["evidence_ids"] == []
    history = await _call(
        registry,
        "search_incident_history",
        {"query": "redis eviction cache hit ratio dropped"},
        store,
    )
    assert "Redis eviction storm" in history["data"]["incidents"][0]["title"]
    assert all(e.startswith("hist-") for e in history["evidence_ids"])
    fetched = await _call(
        registry, "get_evidence", {"evidence_ids": ["topo-orders-service", "dep-9999"]}, store
    )
    assert [e["evidence_id"] for e in fetched["data"]["evidence"]] == ["topo-orders-service"]
    assert fetched["data"]["missing"] == ["dep-9999"] and fetched["warnings"]
