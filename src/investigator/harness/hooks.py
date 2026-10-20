"""Lifecycle hooks around LLM and tool calls: tracing, approvals, redaction, custom metrics."""

from dataclasses import dataclass, field
from typing import Any

from investigator.harness.llm import LLMRequest, LLMResponse, ToolCall


@dataclass
class ToolCallRecord:
    tool: str
    arguments: dict[str, Any]
    ok: bool = False
    error_type: str | None = None
    attempts: int = 0
    latency_s: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)
    partial: bool = False


class Hook:
    """Base class with no-op methods. Subclass it and override what you need."""

    async def before_llm(self, request: LLMRequest, *, task_id: str | None) -> None:
        return None

    async def after_llm(
        self, request: LLMRequest, response: LLMResponse, *, task_id: str | None
    ) -> None:
        return None

    async def on_llm_error(
        self, request: LLMRequest, error: Exception, *, task_id: str | None
    ) -> None:
        return None

    async def before_tool(self, call: ToolCall, *, agent: str, task_id: str | None) -> None:
        return None

    async def after_tool(
        self, call: ToolCall, record: ToolCallRecord, *, agent: str, task_id: str | None
    ) -> None:
        return None


class HookRunner:
    def __init__(self, hooks: list[Hook] | None = None) -> None:
        self.hooks: list[Hook] = list(hooks or [])

    def add(self, hook: Hook) -> None:
        self.hooks.append(hook)

    async def before_llm(self, request: LLMRequest, *, task_id: str | None) -> None:
        for hook in self.hooks:
            await hook.before_llm(request, task_id=task_id)

    async def after_llm(
        self, request: LLMRequest, response: LLMResponse, *, task_id: str | None
    ) -> None:
        for hook in self.hooks:
            await hook.after_llm(request, response, task_id=task_id)

    async def on_llm_error(
        self, request: LLMRequest, error: Exception, *, task_id: str | None
    ) -> None:
        for hook in self.hooks:
            await hook.on_llm_error(request, error, task_id=task_id)

    async def before_tool(self, call: ToolCall, *, agent: str, task_id: str | None) -> None:
        for hook in self.hooks:
            await hook.before_tool(call, agent=agent, task_id=task_id)

    async def after_tool(
        self, call: ToolCall, record: ToolCallRecord, *, agent: str, task_id: str | None
    ) -> None:
        for hook in self.hooks:
            await hook.after_tool(call, record, agent=agent, task_id=task_id)
