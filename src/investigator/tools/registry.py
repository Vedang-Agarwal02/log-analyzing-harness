"""Builds the registry of all domain tools for one investigation environment."""

from investigator.backends.base import Backends
from investigator.harness.tools import ToolRegistry
from investigator.tools.changes import build_change_tools
from investigator.tools.evidence_tool import build_evidence_tools
from investigator.tools.history import build_history_tools
from investigator.tools.logs import build_log_tools
from investigator.tools.metrics import build_metric_tools
from investigator.tools.topology import build_topology_tools

DATA_TOOL_NAMES: tuple[str, ...] = (
    "detect_anomalies",
    "get_config_changes",
    "get_deployments",
    "get_evidence",
    "get_service_dependencies",
    "list_metrics",
    "list_services",
    "query_logs",
    "query_metrics",
    "search_incident_history",
)


def build_registry(backends: Backends) -> ToolRegistry:
    registry = ToolRegistry()
    for item in [
        *build_log_tools(backends),
        *build_metric_tools(backends),
        *build_change_tools(backends),
        *build_topology_tools(backends),
        *build_history_tools(backends),
        *build_evidence_tools(),
    ]:
        registry.register(item)
    return registry
