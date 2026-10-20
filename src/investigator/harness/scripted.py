"""Deterministic fake LLM for tests: per-agent responder functions instead of a model.

A responder is a pure function of the request. `sequence(...)` picks a step by how many
assistant turns the transcript already has, so it is stateless and safe under parallelism.
"""

import asyncio
import dataclasses
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from investigator.harness.llm import (
    AssistantMessage,
    LLMError,
    LLMRequest,
    LLMResponse,
    ToolCall,
    ToolResultPart,
    Usage,
    UserMessage,
    UserText,
)

Responder = Callable[[LLMRequest], LLMResponse | Awaitable[LLMResponse]]
Step = LLMResponse | Callable[[LLMRequest], LLMResponse]

DEFAULT_USAGE = Usage(input_tokens=1000, output_tokens=200)
EVIDENCE_ID_RE = re.compile(r"\b(?:logpat|log|met|dep|cfg|topo|hist)-[A-Za-z0-9][A-Za-z0-9-]*")


def tool_call_response(
    *calls: tuple[str, dict[str, Any]], usage: Usage | None = None
) -> LLMResponse:
    tool_calls = tuple(
        ToolCall(id=f"toolu_{index}", name=name, arguments=args)
        for index, (name, args) in enumerate(calls)
    )
    raw = tuple(
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in tool_calls
    )
    return LLMResponse(
        raw=raw,
        text="",
        tool_calls=tool_calls,
        stop_reason="tool_use",
        usage=usage or DEFAULT_USAGE,
        model="scripted",
    )


def text_response(
    text: str, *, stop_reason: str = "end_turn", usage: Usage | None = None
) -> LLMResponse:
    return LLMResponse(
        raw=({"type": "text", "text": text},),
        text=text,
        tool_calls=(),
        stop_reason=stop_reason,
        usage=usage or DEFAULT_USAGE,
        model="scripted",
    )


def turn_index(request: LLMRequest) -> int:
    return sum(1 for message in request.messages if isinstance(message, AssistantMessage))


def first_user_text(request: LLMRequest) -> str:
    for message in request.messages:
        if isinstance(message, UserMessage):
            return "\n".join(part.text for part in message.parts if isinstance(part, UserText))
    return ""


def last_tool_results(request: LLMRequest) -> list[dict[str, Any]]:
    if not request.messages or not isinstance(request.messages[-1], UserMessage):
        return []
    results: list[dict[str, Any]] = []
    for part in request.messages[-1].parts:
        if isinstance(part, ToolResultPart):
            try:
                results.append(dict(json.loads(part.content)))
            except json.JSONDecodeError:
                results.append({"ok": False, "raw": part.content})
    return results


def result_evidence_ids(request: LLMRequest) -> list[str]:
    ids: set[str] = set()
    for result in last_tool_results(request):
        if result.get("ok"):
            ids.update(str(item) for item in result.get("evidence_ids", []))
    return sorted(ids)


def evidence_ids_in(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in EVIDENCE_ID_RE.findall(text):
        seen.setdefault(match.rstrip("-."), None)
    return list(seen)


def sequence(*steps: Step) -> Responder:
    if not steps:
        raise ValueError("sequence() needs at least one step")

    def responder(request: LLMRequest) -> LLMResponse:
        step = steps[min(turn_index(request), len(steps) - 1)]
        return step if isinstance(step, LLMResponse) else step(request)

    return responder


class ScriptedLLM:
    def __init__(self, responders: Mapping[str, Responder], *, delay_s: float = 0.0) -> None:
        self._responders = dict(responders)
        self._delay_s = delay_s
        self.calls: list[LLMRequest] = []
        self.in_flight = 0
        self.max_in_flight = 0

    def calls_for(self, agent: str) -> list[LLMRequest]:
        return [call for call in self.calls if call.agent_name == agent]

    async def complete(self, request: LLMRequest) -> LLMResponse:
        responder = self._responders.get(request.agent_name) or self._responders.get("*")
        if responder is None:
            raise LLMError(
                f"no scripted responder for agent {request.agent_name!r}", retryable=False
            )
        self.calls.append(request)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self._delay_s > 0:
                await asyncio.sleep(self._delay_s)
            produced = responder(request)
            if inspect.isawaitable(produced):
                response: LLMResponse = await produced
            else:
                response = cast(LLMResponse, produced)
            return _with_unique_ids(response, request)
        finally:
            self.in_flight -= 1


def _with_unique_ids(response: LLMResponse, request: LLMRequest) -> LLMResponse:
    if not response.tool_calls:
        return response
    turn = turn_index(request)
    calls = tuple(
        ToolCall(
            id=f"toolu_{request.agent_name}_{turn}_{index}",
            name=call.name,
            arguments=call.arguments,
        )
        for index, call in enumerate(response.tool_calls)
    )
    raw = tuple(
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in calls
    )
    return dataclasses.replace(response, raw=raw, tool_calls=calls)
