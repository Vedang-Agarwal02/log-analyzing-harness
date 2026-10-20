"""Tool abstractions: the Tool protocol, function tools, toolsets and the registry.

Any object that satisfies `Tool` can be registered: native async functions (via `@tool`),
adapters around MCP servers, HTTP APIs, and so on. Agents only see tools by name.
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from investigator.domain.evidence import Evidence, EvidenceStore
from investigator.harness.llm import ToolSpec
from investigator.harness.schema import model_input_schema


@dataclass(frozen=True)
class ToolContext:
    """Per-call context. `caller` names the agent run (used for fault counters and traces)."""

    caller: str
    evidence: EvidenceStore
    call_id: str = ""


class ToolOutput(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    partial: bool = False
    warnings: list[str] = Field(default_factory=list)


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool
    timeout_s: float | None

    def parse_arguments(self, arguments: dict[str, Any]) -> Any: ...

    async def run(self, arguments: Any, ctx: ToolContext) -> ToolOutput: ...


ToolFunction = Callable[[Any, ToolContext], Awaitable[ToolOutput]]


class FunctionTool:
    """A tool backed by an async function that takes a validated Pydantic model."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_model: type[BaseModel],
        func: ToolFunction,
        read_only: bool = True,
        timeout_s: float | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.input_model = input_model
        self.input_schema = model_input_schema(input_model)
        self.read_only = read_only
        self.timeout_s = timeout_s
        self._func = func

    def parse_arguments(self, arguments: dict[str, Any]) -> BaseModel:
        return self.input_model.model_validate(arguments)

    async def run(self, arguments: Any, ctx: ToolContext) -> ToolOutput:
        return await self._func(arguments, ctx)


def tool(
    *,
    name: str,
    description: str,
    input_model: type[BaseModel],
    read_only: bool = True,
    timeout_s: float | None = None,
) -> Callable[[ToolFunction], FunctionTool]:
    """Decorator that turns `async def f(args: Model, ctx: ToolContext) -> ToolOutput` into a tool."""

    def decorate(func: ToolFunction) -> FunctionTool:
        return FunctionTool(
            name=name,
            description=description,
            input_model=input_model,
            func=func,
            read_only=read_only,
            timeout_s=timeout_s,
        )

    return decorate


class Toolset(Protocol):
    """Anything that provides tools: a module of native tools, an MCP server adapter, ..."""

    def tools(self) -> list[Tool]: ...


def tool_spec(item: Tool) -> ToolSpec:
    return ToolSpec(name=item.name, description=item.description, input_schema=item.input_schema)


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for item in tools:
            self.register(item)

    def register(self, item: Tool) -> None:
        if item.name in self._tools:
            raise ValueError(f"tool {item.name!r} is already registered")
        self._tools[item.name] = item

    def register_toolset(self, toolset: Toolset) -> None:
        for item in toolset.tools():
            self.register(item)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def specs(self) -> list[ToolSpec]:
        return [tool_spec(self._tools[name]) for name in self.names()]

    def subset(self, names: Iterable[str]) -> "ToolRegistry":
        wanted = set(names)
        unknown = sorted(wanted - set(self._tools))
        if unknown:
            raise KeyError(f"unknown tools: {', '.join(unknown)}")
        return ToolRegistry(self._tools[name] for name in sorted(wanted))
