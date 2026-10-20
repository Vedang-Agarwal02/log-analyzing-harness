import asyncio

import pytest

from investigator.harness.llm import (
    AssistantMessage,
    LLMError,
    LLMRequest,
    ToolResultPart,
    UserMessage,
    UserText,
)
from investigator.harness.scripted import (
    ScriptedLLM,
    evidence_ids_in,
    first_user_text,
    last_tool_results,
    result_evidence_ids,
    sequence,
    text_response,
    tool_call_response,
    turn_index,
)


def _request(agent: str, messages: tuple = ()) -> LLMRequest:  # type: ignore[type-arg]
    base = (UserMessage(parts=(UserText("hello R1-T1 dep-0004 and met-1a2b3c4d."),)),)
    return LLMRequest(agent, "scripted", "sys", base + messages, (), 100, "medium")


def test_helpers_read_requests() -> None:
    results = UserMessage(
        parts=(
            ToolResultPart("t1", '{"ok": true, "evidence_ids": ["met-1", "log-2"]}'),
            ToolResultPart("t2", "not json", True),
        )
    )
    request = _request("a", (AssistantMessage(raw=()), results))
    assert turn_index(request) == 1
    assert first_user_text(request).startswith("hello")
    assert last_tool_results(request)[1]["ok"] is False
    assert result_evidence_ids(request) == ["log-2", "met-1"]
    assert evidence_ids_in("see dep-0004, met-1a2b3c4d. and topo-orders-service") == [
        "dep-0004",
        "met-1a2b3c4d",
        "topo-orders-service",
    ]


async def test_sequence_and_unique_tool_ids() -> None:
    llm = ScriptedLLM(
        {"a": sequence(tool_call_response(("x", {}), ("y", {})), text_response("done"))}
    )
    first = await llm.complete(_request("a"))
    assert [call.id for call in first.tool_calls] == ["toolu_a_0_0", "toolu_a_0_1"]
    assert first.raw[0]["id"] == "toolu_a_0_0"
    second = await llm.complete(_request("a", (AssistantMessage(raw=()),)))
    assert second.text == "done"
    third = await llm.complete(_request("a", (AssistantMessage(raw=()), AssistantMessage(raw=()))))
    assert third.text == "done"
    assert len(llm.calls_for("a")) == 3


async def test_missing_responder_raises_and_wildcard_works() -> None:
    llm = ScriptedLLM({"*": sequence(text_response("any"))})
    assert (await llm.complete(_request("whoever"))).text == "any"
    with pytest.raises(LLMError):
        await ScriptedLLM({}).complete(_request("nobody"))


async def test_tracks_concurrency() -> None:
    llm = ScriptedLLM({"*": sequence(text_response("x"))}, delay_s=0.05)
    await asyncio.gather(llm.complete(_request("a")), llm.complete(_request("b")))
    assert llm.max_in_flight == 2 and llm.in_flight == 0
