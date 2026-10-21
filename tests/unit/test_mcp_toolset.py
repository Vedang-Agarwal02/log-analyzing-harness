import json
from dataclasses import dataclass, field
from typing import Any

from investigator.domain.evidence import EvidenceStore
from investigator.harness.budget import Budget, BudgetLimits
from investigator.harness.executor import ToolExecutor
from investigator.harness.hooks import HookRunner
from investigator.harness.llm import ToolCall
from investigator.harness.tools import Tool, ToolContext, ToolRegistry
from investigator.tools.mcp_toolset import MCPToolset, mcp_tool_name


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Result:
    content: list[_Block]
    isError: bool = False
    structuredContent: dict[str, Any] | None = None


@dataclass
class _ToolInfo:
    name: str
    description: str | None
    inputSchema: dict[str, Any]


@dataclass
class _Listed:
    tools: list[_ToolInfo]


@dataclass
class FakeSession:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def list_tools(self) -> _Listed:
        search_schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        return _Listed(
            [
                _ToolInfo("search_runbooks", "Search runbooks.", search_schema),
                _ToolInfo("fail", None, {"type": "object", "properties": {}}),
            ]
        )

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> _Result:
        self.calls.append((name, dict(arguments or {})))
        if name == "fail":
            return _Result([_Block("backend exploded")], isError=True)
        return _Result(
            [_Block(f"Runbook for {(arguments or {})['query']}: restart the pool.")],
            structuredContent={"hits": 1},
        )


async def _setup(session: FakeSession) -> tuple[ToolExecutor, ToolRegistry]:
    registry = ToolRegistry()
    registry.register_toolset(await MCPToolset.from_session("runbooks", session))
    return ToolExecutor(registry, budget=Budget(BudgetLimits()), hooks=HookRunner()), registry


def test_tool_names_are_valid_claude_tool_names() -> None:
    assert mcp_tool_name("run.books", "search runbooks") == "mcp__run_books__search_runbooks"
    assert len(mcp_tool_name("s" * 40, "t" * 40)) == 64


async def test_toolset_wraps_each_server_tool() -> None:
    _, registry = await _setup(FakeSession())
    assert registry.names() == ["mcp__runbooks__fail", "mcp__runbooks__search_runbooks"]
    search = registry.get("mcp__runbooks__search_runbooks")
    assert (
        search is not None
        and isinstance(search, Tool)
        and search.input_schema["required"] == ["query"]
    )
    fail = registry.get("mcp__runbooks__fail")
    assert fail is not None and fail.description == "Tool 'fail' from the runbooks MCP server."


async def test_successful_call_mints_external_evidence() -> None:
    session = FakeSession()
    executor, _ = await _setup(session)
    store = EvidenceStore()
    call = ToolCall("c1", "mcp__runbooks__search_runbooks", {"query": "pool exhaustion"})
    envelope = json.loads(
        (
            await executor.execute(
                call, ToolContext(caller="t", evidence=store), agent="t", task_id=None
            )
        ).content
    )
    assert envelope["ok"] is True and envelope["data"]["structured"] == {"hits": 1}
    [evidence_id] = envelope["evidence_ids"]
    stored = store.get(evidence_id)
    assert evidence_id.startswith("mcp-") and stored is not None and stored.kind == "external"
    assert session.calls == [("search_runbooks", {"query": "pool exhaustion"})]


async def test_missing_arguments_and_server_errors_become_error_envelopes() -> None:
    executor, _ = await _setup(FakeSession())
    ctx = ToolContext(caller="t", evidence=EvidenceStore())
    missing = json.loads(
        (
            await executor.execute(
                ToolCall("c1", "mcp__runbooks__search_runbooks", {}), ctx, agent="t", task_id=None
            )
        ).content
    )
    assert missing["ok"] is False and missing["error"]["type"] == "invalid_arguments"
    failed = json.loads(
        (
            await executor.execute(
                ToolCall("c2", "mcp__runbooks__fail", {}), ctx, agent="t", task_id=None
            )
        ).content
    )
    assert failed["ok"] is False and failed["error"]["type"] == "mcp_error"
    assert "backend exploded" in failed["error"]["message"]
