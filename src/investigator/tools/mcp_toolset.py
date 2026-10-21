"""Optional adapter that exposes an MCP server's tools through the Tool protocol.

The `mcp` package (install with `uv sync --extra mcp`) is imported only inside `connect_stdio`, so the
rest of the system runs without it. Each successful call becomes one `external` evidence item.
"""

import hashlib
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Protocol

from investigator.domain.evidence import Evidence
from investigator.errors import InvalidInput, OperationError
from investigator.harness.tools import Tool, ToolContext, ToolOutput
from investigator.jsonutil import canonical_json

MAX_TEXT = 4000
_INVALID_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


class MCPCallFailed(OperationError):
    error_type = "mcp_error"
    retryable = False


class MCPSession(Protocol):
    """The part of `mcp.ClientSession` this adapter uses."""

    async def list_tools(self) -> Any: ...

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any: ...


def mcp_tool_name(server: str, tool: str) -> str:
    """Claude tool names allow only letters, digits, '_' and '-', up to 64 characters."""
    return _INVALID_NAME_CHARS.sub("_", f"mcp__{server}__{tool}")[:64]


def _text_of(result: Any) -> str:
    blocks = getattr(result, "content", None) or []
    parts = [
        str(getattr(block, "text", "")) for block in blocks if getattr(block, "type", "") == "text"
    ]
    return "\n".join(part for part in parts if part)


class MCPTool:
    read_only = True

    def __init__(
        self,
        *,
        server: str,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        session: MCPSession,
        timeout_s: float | None = None,
    ) -> None:
        self.server = server
        self.remote_name = name
        self.name = mcp_tool_name(server, name)
        self.description = description or f"Tool {name!r} from the {server} MCP server."
        self.input_schema = input_schema or {"type": "object", "properties": {}}
        self.timeout_s = timeout_s
        self._session = session

    def parse_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return dict(arguments)

    async def run(self, arguments: Any, ctx: ToolContext) -> ToolOutput:
        missing = sorted(set(self.input_schema.get("required", [])) - set(arguments))
        if missing:
            raise InvalidInput(
                f"missing required arguments: {', '.join(missing)}",
                hint="Match the tool's input schema.",
            )
        result = await self._session.call_tool(self.remote_name, arguments)
        text = _text_of(result)
        if getattr(result, "isError", False):
            raise MCPCallFailed(f"{self.name} returned an error: {text[:500]}")
        digest = hashlib.sha1(
            f"{self.server}|{self.remote_name}|{canonical_json(arguments)}".encode()
        ).hexdigest()[:10]
        evidence = Evidence(
            evidence_id=f"mcp-{digest}",
            kind="external",
            summary=f"{self.server}/{self.remote_name}: {text[:200]}",
            source_tool=self.name,
            attributes={
                "server": self.server,
                "tool": self.remote_name,
                "arguments": arguments,
                "text": text[:MAX_TEXT],
            },
        )
        truncated = len(text) > MAX_TEXT
        data: dict[str, Any] = {"text": text[:MAX_TEXT], "truncated": truncated}
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            data["structured"] = structured
        warnings = ["The output was truncated."] if truncated else []
        return ToolOutput(data=data, evidence=[evidence], partial=truncated, warnings=warnings)


class MCPToolset:
    def __init__(self, server: str, tools: Sequence[MCPTool]) -> None:
        self.server = server
        self._tools: list[Tool] = list(tools)

    def tools(self) -> list[Tool]:
        return list(self._tools)

    @classmethod
    async def from_session(
        cls, server: str, session: MCPSession, *, timeout_s: float | None = None
    ) -> "MCPToolset":
        listed = await session.list_tools()
        tools = [
            MCPTool(
                server=server,
                name=item.name,
                description=item.description or "",
                input_schema=dict(item.inputSchema or {}),
                session=session,
                timeout_s=timeout_s,
            )
            for item in sorted(listed.tools, key=lambda item: item.name)
        ]
        return cls(server, tools)


@asynccontextmanager
async def connect_stdio(
    server: str, command: str, args: Sequence[str] = (), env: dict[str, str] | None = None
) -> AsyncIterator[MCPToolset]:
    """Start an MCP server as a subprocess and yield its tools while the connection is open."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=command, args=list(args), env=env)
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield await MCPToolset.from_session(server, session)
