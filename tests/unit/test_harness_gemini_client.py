import dataclasses
import json
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors, types

from investigator.harness.gemini_client import (
    EMPTY_TURN_TEXT,
    GeminiClient,
    GeminiSettings,
    build_config,
    gemini_schema,
    parse_response,
    part_from_dict,
    part_to_dict,
    thinking_config,
    to_gemini_contents,
)
from investigator.harness.llm import (
    AssistantMessage,
    LLMError,
    LLMRequest,
    ToolCall,
    ToolResultPart,
    ToolSpec,
    UserMessage,
    UserText,
    assistant_message,
)

SIGNATURE = b"\x00\xffopaque-signature"
TOOL = ToolSpec(
    name="query_logs",
    description="Search logs.",
    input_schema={
        "type": "object",
        "properties": {
            "service": {"type": "string", "minLength": 1, "description": "Service name"},
            "level": {
                "anyOf": [{"type": "string", "enum": ["WARN", "ERROR"]}, {"type": "null"}],
                "default": None,
            },
        },
        "required": ["service"],
        "additionalProperties": False,
    },
)


def _request(*messages: Any, model: str = "gemini-4-argon", effort: str = "medium") -> LLMRequest:
    first = UserMessage(parts=(UserText("Investigate."),))
    return LLMRequest(
        agent_name="observability",
        model=model,
        system="You are careful.",
        messages=(first, *messages),
        tools=(TOOL,),
        max_tokens=32000,
        effort=effort,
    )


def _response(
    *parts: types.Part, finish: str = "STOP", **usage: int
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=list(parts)),
                finish_reason=types.FinishReason(finish),
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(**usage),
        model_version="gemini-4-argon",
    )


def _call(
    name: str, call_id: str | None, signature: bytes | None = None, **args: Any
) -> types.Part:
    return types.Part(
        function_call=types.FunctionCall(id=call_id, name=name, args=args),
        thought_signature=signature,
    )


class FakeModels:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> types.GenerateContentResponse:
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _client(*results: Any) -> tuple[GeminiClient, FakeModels]:
    models = FakeModels(*results)
    return GeminiClient(
        GeminiSettings(), client=SimpleNamespace(aio=SimpleNamespace(models=models))
    ), models


def test_thinking_config_maps_effort_per_model_family() -> None:
    assert thinking_config("gemini-4-argon", "low").thinking_level == types.ThinkingLevel.LOW
    assert (
        thinking_config("gemini-3.8-flash", "medium").thinking_level == types.ThinkingLevel.MEDIUM
    )
    for effort in ("high", "xhigh", "max"):
        config = thinking_config("gemini-4-argon", effort)
        assert config.thinking_level == types.ThinkingLevel.HIGH and config.thinking_budget is None
    legacy = thinking_config("gemini-2.5-pro", "low")
    assert legacy.thinking_budget == 1024 and legacy.thinking_level is None
    assert thinking_config("gemini-2.5-flash", "high").thinking_budget == -1


def test_gemini_schema_keeps_only_supported_keywords() -> None:
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "default": "x"},
            "kind": {"const": "a"},
            "when": {
                "anyOf": [{"type": "string", "format": "date-time"}, {"type": "null"}],
                "default": None,
            },
            "tags": {
                "type": "array",
                "items": {"type": "string", "pattern": "^[a-z]+$"},
                "minItems": 1,
            },
        },
        "required": ["title"],
        "additionalProperties": False,
    }
    assert gemini_schema(schema) == {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "kind": {"enum": ["a"]},
            "when": {"anyOf": [{"type": "string", "format": "date-time"}, {"type": "null"}]},
            "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        },
        "required": ["title"],
        "additionalProperties": False,
    }


def test_build_config_declares_tools_and_disables_automatic_calls() -> None:
    config = build_config(_request(), GeminiSettings(tool_mode="VALIDATED"))
    assert config.system_instruction == "You are careful." and config.max_output_tokens == 32000
    assert (
        config.thinking_config is not None
        and config.thinking_config.thinking_level == types.ThinkingLevel.MEDIUM
    )
    assert config.tools is not None
    [tool] = config.tools
    assert isinstance(tool, types.Tool) and tool.function_declarations is not None
    [declaration] = tool.function_declarations
    assert declaration.name == "query_logs"
    assert declaration.parameters_json_schema == gemini_schema(TOOL.input_schema)
    assert config.tool_config is not None and config.tool_config.function_calling_config is not None
    assert (
        config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    )
    assert (
        config.automatic_function_calling is not None
        and config.automatic_function_calling.disable is True
    )
    assert config.temperature is None and config.top_p is None


def test_parts_round_trip_losslessly_through_json() -> None:
    original = _call("query_logs", "call-1", SIGNATURE, service="orders-service")
    stored = part_to_dict(original)
    restored = part_from_dict(json.loads(json.dumps(stored)))
    assert restored.thought_signature == SIGNATURE
    assert restored.function_call is not None and restored.function_call.args == {
        "service": "orders-service"
    }
    text_with_signature = types.Part(text="", thought_signature=SIGNATURE)
    assert part_from_dict(part_to_dict(text_with_signature)).thought_signature == SIGNATURE


def test_parse_response_maps_calls_text_usage_and_model() -> None:
    response = _response(
        types.Part(text="private reasoning", thought=True),
        types.Part(text="Checking the logs."),
        _call("query_logs", "call-1", SIGNATURE, service="orders-service"),
        prompt_token_count=1000,
        cached_content_token_count=400,
        candidates_token_count=50,
        thoughts_token_count=30,
    )
    parsed = parse_response(response, _request(), latency_s=0.5)
    assert parsed.text == "Checking the logs."
    assert parsed.tool_calls == (
        ToolCall(id="call-1", name="query_logs", arguments={"service": "orders-service"}),
    )
    assert parsed.stop_reason == "tool_use" and parsed.model == "gemini-4-argon"
    assert parsed.usage.input_tokens == 600 and parsed.usage.cache_read_tokens == 400
    assert parsed.usage.output_tokens == 80 and parsed.usage.cache_write_tokens == 0
    assert len(parsed.raw) == 3 and json.dumps(parsed.raw)


def test_missing_call_ids_get_stable_unique_ids() -> None:
    response = _response(
        _call("query_logs", None, service="a"), _call("query_logs", None, service="b")
    )
    first = parse_response(response, _request(), latency_s=0.0)
    again = parse_response(response, _request(), latency_s=0.0)
    other = parse_response(
        response, _request(UserMessage(parts=(UserText("more"),))), latency_s=0.0
    )
    ids = [call.id for call in first.tool_calls]
    assert len(set(ids)) == 2 and all(i.startswith("gm-") for i in ids)
    assert ids == [call.id for call in again.tool_calls]
    assert ids != [call.id for call in other.tool_calls]


@pytest.mark.parametrize(
    ("finish", "parts", "expected"),
    [
        ("SAFETY", (), "refusal"),
        ("PROHIBITED_CONTENT", (), "refusal"),
        ("MAX_TOKENS", (types.Part(text="partial"),), "max_tokens"),
        ("MALFORMED_FUNCTION_CALL", (), "end_turn"),
        ("STOP", (types.Part(text="done"),), "end_turn"),
    ],
)
def test_finish_reasons_map_to_neutral_stop_reasons(
    finish: str, parts: tuple[types.Part, ...], expected: str
) -> None:
    assert (
        parse_response(_response(*parts, finish=finish), _request(), latency_s=0.0).stop_reason
        == expected
    )


def test_blocked_prompt_is_a_refusal() -> None:
    blocked = types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.SAFETY
        )
    )
    parsed = parse_response(blocked, _request(), latency_s=0.0)
    assert parsed.stop_reason == "refusal" and parsed.raw == () and parsed.tool_calls == ()


def test_contents_echo_model_parts_and_group_function_responses() -> None:
    model_turn = AssistantMessage(
        raw=(
            part_to_dict(_call("query_logs", "c1", SIGNATURE, service="a")),
            part_to_dict(_call("list_services", "c2")),
        ),
        tool_calls=(
            ToolCall("c1", "query_logs", {"service": "a"}),
            ToolCall("c2", "list_services", {}),
        ),
    )
    results = UserMessage(
        parts=(
            ToolResultPart("c1", json.dumps({"ok": True, "data": {"patterns": []}})),
            ToolResultPart("c2", "not json", is_error=True),
            UserText("Tool-call limit reached."),
        )
    )
    contents = to_gemini_contents(_request(model_turn, results).messages)
    assert [c.role for c in contents if isinstance(c, types.Content)] == ["user", "model", "user"]
    model = contents[1]
    assert isinstance(model, types.Content) and model.parts is not None
    assert (
        model.parts[0].thought_signature == SIGNATURE and model.parts[1].thought_signature is None
    )
    user = contents[2]
    assert isinstance(user, types.Content) and user.parts is not None
    first, second, note = user.parts
    assert first.function_response is not None and second.function_response is not None
    assert (first.function_response.id, first.function_response.name) == ("c1", "query_logs")
    assert first.function_response.response == {"ok": True, "data": {"patterns": []}}
    assert (second.function_response.id, second.function_response.name) == ("c2", "list_services")
    assert second.function_response.response == {"error": "not json"}
    assert note.text == "Tool-call limit reached."


def test_function_responses_omit_ids_the_model_did_not_send_and_empty_turns_get_text() -> None:
    model_turn = AssistantMessage(
        raw=(part_to_dict(_call("list_services", None)),),
        tool_calls=(ToolCall("gm-x-0", "list_services", {}),),
    )
    results = UserMessage(parts=(ToolResultPart("gm-x-0", json.dumps({"ok": True})),))
    empty = AssistantMessage(raw=())
    contents = to_gemini_contents(_request(model_turn, results, empty).messages)
    reply, placeholder = contents[2], contents[3]
    assert isinstance(reply, types.Content) and reply.parts is not None
    response = reply.parts[0].function_response
    assert response is not None and response.id is None and response.name == "list_services"
    assert isinstance(placeholder, types.Content) and placeholder.parts is not None
    assert placeholder.parts[0].text == EMPTY_TURN_TEXT


async def test_second_request_echoes_the_signature_and_the_call_id() -> None:
    first = _response(_call("query_logs", "call-7", SIGNATURE, service="a"), prompt_token_count=10)
    second = _response(types.Part(text="done"), prompt_token_count=20)
    client, models = _client(first, second)
    request = _request()
    response = await client.complete(request)
    follow_up = dataclasses.replace(
        request,
        messages=(
            *request.messages,
            assistant_message(response),
            UserMessage(parts=(ToolResultPart("call-7", json.dumps({"ok": True})),)),
        ),
    )
    await client.complete(follow_up)
    sent = models.calls[1]["contents"]
    assert models.calls[1]["model"] == "gemini-4-argon"
    assert sent[1].role == "model" and sent[1].parts[0].thought_signature == SIGNATURE
    assert sent[2].parts[0].function_response.id == "call-7"


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (
            errors.ClientError(
                400,
                {"error": {"code": 400, "message": "bad request", "status": "INVALID_ARGUMENT"}},
            ),
            False,
        ),
        (
            errors.ClientError(
                429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}
            ),
            True,
        ),
        (
            errors.ServerError(
                503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}}
            ),
            True,
        ),
        (ConnectionError("network down"), True),
    ],
)
async def test_errors_become_llm_errors(error: Exception, retryable: bool) -> None:
    client, _ = _client(error)
    with pytest.raises(LLMError) as raised:
        await client.complete(_request())
    assert raised.value.retryable is retryable
