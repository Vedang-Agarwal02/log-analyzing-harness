"""Claude adapter: neutral LLMRequest -> Anthropic Messages API (streaming) -> LLMResponse.

Rules for current Claude models (Opus 5.5):
- Do not send `thinking`: adaptive thinking is always on; `output_config.effort` controls depth.
- Never send temperature/top_p/top_k or a forced tool_choice (both return HTTP 400).
- Prompt caching: one breakpoint on the frozen system prompt plus top-level automatic caching.
- Refusal fallback: server-side `fallbacks: "default"` (beta header below), on by default.
- Assistant content blocks are returned verbatim so the agent loop can echo them unchanged.
"""

import time
from dataclasses import dataclass
from typing import Any

import anthropic

from investigator.harness.llm import (
    AssistantMessage,
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    ToolCall,
    ToolResultPart,
    Usage,
    UserText,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
_DROP_BEFORE_FALLBACK = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}


@dataclass(frozen=True)
class AnthropicSettings:
    max_retries: int = 3
    timeout_s: float = 600.0
    refusal_fallback: bool = True
    prompt_caching: bool = True


def to_anthropic_message(message: Message) -> dict[str, Any]:
    if isinstance(message, AssistantMessage):
        return {"role": "assistant", "content": [dict(block) for block in message.raw]}
    content: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, ToolResultPart):
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": part.tool_call_id,
                "content": part.content,
            }
            if part.is_error:
                block["is_error"] = True
            content.append(block)
    for part in message.parts:
        if isinstance(part, UserText):
            content.append({"type": "text", "text": part.text})
    return {"role": "user", "content": content}


def build_params(request: LLMRequest, settings: AnthropicSettings) -> dict[str, Any]:
    system: dict[str, Any] = {"type": "text", "text": request.system}
    if settings.prompt_caching:
        system["cache_control"] = {"type": "ephemeral"}
    params: dict[str, Any] = {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "system": [system],
        "messages": [to_anthropic_message(message) for message in request.messages],
        "tools": [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "eager_input_streaming": True,
            }
            for spec in request.tools
        ],
        "output_config": {"effort": request.effort},
    }
    extra_body: dict[str, Any] = {}
    if settings.prompt_caching:
        extra_body["cache_control"] = {"type": "ephemeral"}
    if settings.refusal_fallback:
        extra_body["fallbacks"] = "default"
    if extra_body:
        params["extra_body"] = extra_body
    return params


def _block_to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return dict(block)
    to_dict = getattr(block, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict(mode="json"))
    return dict(block.model_dump(mode="json", by_alias=True, exclude_none=True))


def parse_message(message: Any, *, latency_s: float) -> LLMResponse:
    blocks = [_block_to_dict(block) for block in message.content]
    fallback_used = any(block.get("type") == "fallback" for block in blocks)
    if fallback_used:
        last = max(index for index, block in enumerate(blocks) if block.get("type") == "fallback")
        blocks = [
            block
            for index, block in enumerate(blocks)
            if block.get("type") != "fallback"
            and not (index < last and block.get("type") in _DROP_BEFORE_FALLBACK)
        ]
    text = "".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text")
    tool_calls = tuple(
        ToolCall(
            id=str(block["id"]), name=str(block["name"]), arguments=dict(block.get("input") or {})
        )
        for block in blocks
        if block.get("type") == "tool_use"
    )
    usage = getattr(message, "usage", None)
    return LLMResponse(
        raw=tuple(blocks),
        text=text,
        tool_calls=tool_calls,
        stop_reason=str(getattr(message, "stop_reason", None) or "end_turn"),
        usage=Usage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        ),
        model=str(getattr(message, "model", "")),
        latency_s=latency_s,
        fallback_used=fallback_used,
    )


class AnthropicClient:
    def __init__(
        self, settings: AnthropicSettings | None = None, client: Any | None = None
    ) -> None:
        self._settings = settings or AnthropicSettings()
        self._client = client or anthropic.AsyncAnthropic(
            max_retries=self._settings.max_retries, timeout=self._settings.timeout_s
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        params = build_params(request, self._settings)
        started = time.perf_counter()
        try:
            if self._settings.refusal_fallback:
                async with self._client.beta.messages.stream(
                    betas=[FALLBACK_BETA], **params
                ) as stream:
                    message = await stream.get_final_message()
            else:
                async with self._client.messages.stream(**params) as stream:
                    message = await stream.get_final_message()
        except anthropic.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc}", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(
                f"API error {exc.status_code}: {exc.message}", retryable=exc.status_code >= 500
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"connection error: {exc}", retryable=True) from exc
        return parse_message(message, latency_s=time.perf_counter() - started)
