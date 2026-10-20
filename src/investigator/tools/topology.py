"""Topology tools: get_service_dependencies and list_services."""

from typing import Any

from pydantic import BaseModel, Field

from investigator.backends.base import Backends
from investigator.domain.evidence import Evidence
from investigator.harness.tools import Tool, ToolContext, ToolOutput, tool


class ServiceDependenciesInput(BaseModel):
    service: str = Field(description="Service name exactly as in the service catalog.")


class ListServicesInput(BaseModel):
    pass


def build_topology_tools(backends: Backends) -> list[Tool]:
    @tool(
        name="get_service_dependencies",
        input_model=ServiceDependenciesInput,
        description=(
            "Show what a service depends on (upstream) and which services depend on it (downstream), with each "
            "node's kind. Call it to trace causal paths and to find shared dependencies behind symptoms in "
            "several services."
        ),
    )
    async def get_service_dependencies(
        args: ServiceDependenciesInput, ctx: ToolContext
    ) -> ToolOutput:
        info = await backends.topology.get_service(args.service)
        catalog = {service.name: service for service in await backends.topology.list_services()}
        dependents = sorted(
            name for name, service in catalog.items() if args.service in service.depends_on
        )

        def node(name: str) -> dict[str, Any]:
            return {"service": name, "kind": catalog[name].kind if name in catalog else "unknown"}

        data = {
            "service": info.name,
            "kind": info.kind,
            "tier": info.tier,
            "team": info.team,
            "description": info.description,
            "depends_on": [node(name) for name in sorted(info.depends_on)],
            "dependents": [node(name) for name in dependents],
        }
        upstream = ", ".join(sorted(info.depends_on)) or "nothing"
        downstream = ", ".join(dependents) or "nothing"
        evidence = Evidence(
            evidence_id=f"topo-{info.name}",
            kind="dependency",
            service=info.name,
            summary=f"{info.name} depends on [{upstream}]; depended on by [{downstream}]",
            source_tool="get_service_dependencies",
            attributes=data,
        )
        return ToolOutput(data=data, evidence=[evidence])

    @tool(
        name="list_services",
        input_model=ListServicesInput,
        description=(
            "List every service, datastore, infrastructure component and external provider in the platform with "
            "kind, team and description. Call it when you need exact service names."
        ),
    )
    async def list_services(args: ListServicesInput, ctx: ToolContext) -> ToolOutput:
        services = await backends.topology.list_services()
        rows = [
            {
                "name": s.name,
                "kind": s.kind,
                "tier": s.tier,
                "team": s.team,
                "description": s.description,
            }
            for s in services
        ]
        return ToolOutput(data={"services": rows})

    return [get_service_dependencies, list_services]
