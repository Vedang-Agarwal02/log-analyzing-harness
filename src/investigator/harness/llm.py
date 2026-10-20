"""Provider-neutral LLM types: transcript messages, requests, responses and the client protocol.

`AssistantMessage.raw` holds the provider's own content blocks (including thinking blocks),
which must be echoed back unchanged on the next request. Transcripts are append-only.
"""

from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class UserText:
    text: str


@dataclass(frozen=True)
class ToolResultPart:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class UserMessage:
    parts: tuple[UserText | ToolResultPart, ...]


@dataclass(frozen=True)
class AssistantMessage:
    raw: tuple[dict[str, Any], ...]
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


Message = UserMessage | AssistantMessage


@dataclass(frozen=True)
class LLMRequest:
    agent_name: str
    model: str
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    max_tokens: int
    effort: str


@dataclass(frozen=True)
class LLMResponse:
    raw: tuple[dict[str, Any], ...]
    text: str
    tool_calls: tuple[ToolCall, ...]
    stop_reason: str
    usage: Usage
    model: str
    latency_s: float = 0.0
    fallback_used: bool = False


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMClient(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse: ...


def assistant_message(response: LLMResponse) -> AssistantMessage:
    return AssistantMessage(raw=response.raw, text=response.text, tool_calls=response.tool_calls)


def messages_to_jsonable(messages: tuple[Message, ...] | list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, AssistantMessage):
            out.append({"role": "assistant", "raw": [dict(block) for block in message.raw]})
            continue
        parts: list[dict[str, Any]] = []
        for part in message.parts:
            if isinstance(part, ToolResultPart):
                parts.append(
                    {
                        "type": "tool_result",
                        "tool_call_id": part.tool_call_id,
                        "content": part.content,
                        "is_error": part.is_error,
                    }
                )
            else:
                parts.append({"type": "text", "text": part.text})
        out.append({"role": "user", "parts": parts})
    return out


def response_to_dict(response: LLMResponse) -> dict[str, Any]:
    return {
        "raw": [dict(block) for block in response.raw],
        "text": response.text,
        "tool_calls": [asdict(call) for call in response.tool_calls],
        "stop_reason": response.stop_reason,
        "usage": response.usage.to_dict(),
        "model": response.model,
        "latency_s": response.latency_s,
        "fallback_used": response.fallback_used,
    }


def response_from_dict(data: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        raw=tuple(dict(block) for block in data["raw"]),
        text=str(data["text"]),
        tool_calls=tuple(
            ToolCall(id=str(c["id"]), name=str(c["name"]), arguments=dict(c["arguments"]))
            for c in data["tool_calls"]
        ),
        stop_reason=str(data["stop_reason"]),
        usage=Usage(**data["usage"]),
        model=str(data["model"]),
        latency_s=float(data.get("latency_s", 0.0)),
        fallback_used=bool(data.get("fallback_used", False)),
    )
