from investigator.callctx import CURRENT_CALLER
from investigator.errors import InvalidInput, OperationError, OperationTimeout, RateLimited
from investigator.harness.llm import (
    AssistantMessage,
    LLMResponse,
    ToolCall,
    ToolResultPart,
    Usage,
    UserMessage,
    UserText,
    assistant_message,
    messages_to_jsonable,
    response_from_dict,
    response_to_dict,
)
from investigator.jsonutil import canonical_json, pretty_json


def test_usage_addition_total_and_dict() -> None:
    total = Usage(10, 5, 100, 20) + Usage(1, 1, 1, 1)
    assert total == Usage(11, 6, 101, 21)
    assert total.total_tokens == 139
    assert total.to_dict() == {
        "input_tokens": 11,
        "output_tokens": 6,
        "cache_read_tokens": 101,
        "cache_write_tokens": 21,
    }


def test_canonical_json_is_sorted_and_compact() -> None:
    assert canonical_json({"b": 1, "a": [2, {"d": 3, "c": 4}]}) == '{"a":[2,{"c":4,"d":3}],"b":1}'
    assert pretty_json({"b": 1, "a": 2}).startswith('{\n  "a": 2')


def test_operation_errors_carry_type_retryability_and_hint() -> None:
    timeout = OperationTimeout("slow", hint="narrow the window")
    assert isinstance(timeout, OperationError)
    assert (timeout.error_type, timeout.retryable, timeout.hint) == (
        "timeout",
        True,
        "narrow the window",
    )
    assert RateLimited("busy", retry_after_s=0.5).retry_after_s == 0.5
    assert InvalidInput("bad").retryable is False
    assert InvalidInput("bad").error_type == "invalid_arguments"


def test_current_caller_default() -> None:
    assert CURRENT_CALLER.get() == "anonymous"


def test_response_round_trip_and_assistant_message() -> None:
    response = LLMResponse(
        raw=({"type": "tool_use", "id": "t1", "name": "x", "input": {"a": 1}},),
        text="",
        tool_calls=(ToolCall(id="t1", name="x", arguments={"a": 1}),),
        stop_reason="tool_use",
        usage=Usage(1, 2, 3, 4),
        model="m",
        latency_s=0.5,
    )
    assert response_from_dict(response_to_dict(response)) == response
    message = assistant_message(response)
    assert message.tool_calls[0].name == "x" and message.raw == response.raw


def test_messages_to_jsonable() -> None:
    messages = [
        UserMessage(parts=(UserText("hi"),)),
        AssistantMessage(raw=({"type": "text", "text": "ok"},), text="ok"),
        UserMessage(parts=(ToolResultPart("t1", "{}", True), UserText("more"))),
    ]
    assert messages_to_jsonable(messages) == [
        {"role": "user", "parts": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "raw": [{"type": "text", "text": "ok"}]},
        {
            "role": "user",
            "parts": [
                {"type": "tool_result", "tool_call_id": "t1", "content": "{}", "is_error": True},
                {"type": "text", "text": "more"},
            ],
        },
    ]
