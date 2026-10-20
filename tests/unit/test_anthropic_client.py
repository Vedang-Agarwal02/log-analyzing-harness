from types import SimpleNamespace
from typing import Any

from investigator.harness.anthropic_client import (
    FALLBACK_BETA,
    AnthropicClient,
    AnthropicSettings,
    build_params,
    parse_message,
)
from investigator.harness.llm import (
    AssistantMessage,
    LLMRequest,
    ToolResultPart,
    ToolSpec,
    UserMessage,
    UserText,
)


class FakeBlock:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def to_dict(self, mode: str = "python") -> dict[str, Any]:
        return dict(self._data)


class FakeStream:
    def __init__(self, message: Any) -> None:
        self._message = message

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get_final_message(self) -> Any:
        return self._message


class FakeMessages:
    def __init__(self, message: Any) -> None:
        self.message = message
        self.params: dict[str, Any] | None = None

    def stream(self, **params: Any) -> FakeStream:
        self.params = params
        return FakeStream(self.message)


class FakeSDK:
    def __init__(self, message: Any) -> None:
        self.messages = FakeMessages(message)
        self.beta = SimpleNamespace(messages=FakeMessages(message))


def _message(*blocks: dict[str, Any], stop_reason: str = "tool_use") -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        cache_read_input_tokens=100,
        cache_creation_input_tokens=None,
    )
    return SimpleNamespace(
        content=[FakeBlock(block) for block in blocks],
        stop_reason=stop_reason,
        model="claude-opus-5-5",
        usage=usage,
    )


REQUEST = LLMRequest(
    agent_name="observability",
    model="claude-opus-5-5",
    system="You investigate.",
    messages=(
        UserMessage(parts=(UserText("go"),)),
        AssistantMessage(
            raw=(
                {"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "tool_use", "id": "t1", "name": "q", "input": {}},
            )
        ),
        UserMessage(parts=(UserText("note"), ToolResultPart("t1", '{"ok":true}', True))),
    ),
    tools=(ToolSpec("q", "Query.", {"type": "object", "properties": {}}),),
    max_tokens=32000,
    effort="medium",
)


def test_build_params_shape() -> None:
    params = build_params(REQUEST, AnthropicSettings())
    assert params["model"] == "claude-opus-5-5" and params["max_tokens"] == 32000
    assert params["system"] == [
        {"type": "text", "text": "You investigate.", "cache_control": {"type": "ephemeral"}}
    ]
    assert params["output_config"] == {"effort": "medium"}
    assert params["tools"][0]["eager_input_streaming"] is True
    assert params["extra_body"] == {"cache_control": {"type": "ephemeral"}, "fallbacks": "default"}
    for forbidden in ("temperature", "top_p", "top_k", "thinking", "tool_choice"):
        assert forbidden not in params
    assert params["messages"][1] == {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "tool_use", "id": "t1", "name": "q", "input": {}},
        ],
    }
    user = params["messages"][2]["content"]
    assert user[0] == {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": '{"ok":true}',
        "is_error": True,
    }
    assert user[1] == {"type": "text", "text": "note"}


def test_build_params_without_caching_or_fallback() -> None:
    params = build_params(REQUEST, AnthropicSettings(prompt_caching=False, refusal_fallback=False))
    assert "extra_body" not in params
    assert "cache_control" not in params["system"][0]


def test_parse_message_keeps_blocks_and_usage() -> None:
    message = _message(
        {"type": "thinking", "thinking": "", "signature": "s"},
        {"type": "text", "text": "Checking."},
        {"type": "tool_use", "id": "t9", "name": "q", "input": {"a": 1}},
    )
    response = parse_message(message, latency_s=1.5)
    assert response.raw[0]["type"] == "thinking" and response.text == "Checking."
    assert response.tool_calls[0].arguments == {"a": 1}
    assert response.usage.cache_read_tokens == 100 and response.usage.cache_write_tokens == 0
    assert response.fallback_used is False and response.model == "claude-opus-5-5"
    assert response.latency_s == 1.5


def test_parse_message_strips_blocks_before_fallback() -> None:
    message = _message(
        {"type": "thinking", "thinking": "", "signature": "s"},
        {"type": "tool_use", "id": "t1", "name": "q", "input": {}},
        {"type": "text", "text": "partial "},
        {"type": "fallback", "from": {"model": "a"}, "to": {"model": "b"}},
        {"type": "text", "text": "rest"},
        {"type": "tool_use", "id": "t2", "name": "q", "input": {}},
    )
    response = parse_message(message, latency_s=0.0)
    assert [block["type"] for block in response.raw] == ["text", "text", "tool_use"]
    assert [call.id for call in response.tool_calls] == ["t2"]
    assert response.fallback_used is True


async def test_complete_uses_beta_stream_when_fallback_enabled() -> None:
    sdk = FakeSDK(_message({"type": "text", "text": "hi"}, stop_reason="end_turn"))
    response = await AnthropicClient(AnthropicSettings(), client=sdk).complete(REQUEST)
    assert response.text == "hi" and response.stop_reason == "end_turn"
    assert sdk.beta.messages.params is not None
    assert sdk.beta.messages.params["betas"] == [FALLBACK_BETA]
    assert sdk.messages.params is None


async def test_complete_uses_plain_stream_without_fallback() -> None:
    sdk = FakeSDK(_message({"type": "text", "text": "hi"}, stop_reason="end_turn"))
    await AnthropicClient(AnthropicSettings(refusal_fallback=False), client=sdk).complete(REQUEST)
    assert sdk.messages.params is not None and "betas" not in sdk.messages.params
