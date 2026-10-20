"""Gemini adapter: neutral LLMRequest -> Gemini API (generateContent) -> LLMResponse.

Rules for current Gemini models (Gemini 4 and 3.x; Gemini 2.5 where noted):
- Effort maps to `thinking_level` (low / medium / high). `minimal` is never sent: Gemini 4 Argon and
  Gemini 3.8 Flash reject it. Gemini 2.5 models take `thinking_budget` instead; sending both is an HTTP 400.
- Thought signatures ride on response parts and must come back unchanged, in the same part and order.
  Every response part is stored in `raw` and rebuilt verbatim; signature bytes travel as base64.
- A function response carries the exact id of its function call. Responses to parallel calls go together
  in one user turn, after the calls, never interleaved with them.
- Sampling parameters stay at their defaults, and automatic function calling is off: the harness runs tools.
- Tool schemas are reduced to the JSON Schema subset Gemini accepts; our validators still enforce the rest.
- Retries for 408/429/5xx and transient network errors are delegated to the SDK (`HttpRetryOptions`).
- This uses generateContent, which Google keeps fully supported. Moving to its newer Interactions API would
  only change this file.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

from google import genai
from google.genai import errors, types

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
    messages_to_jsonable,
)
from investigator.jsonutil import canonical_json

ToolMode = Literal["AUTO", "VALIDATED"]

EMPTY_TURN_TEXT = "(empty response)"
_LEVELS = {"low": types.ThinkingLevel.LOW, "medium": types.ThinkingLevel.MEDIUM}
_BUDGETS_2_5 = {"low": 1024, "medium": 8192}
_SCHEMA_KEYS = frozenset(
    {
        "type",
        "title",
        "description",
        "properties",
        "required",
        "additionalProperties",
        "enum",
        "format",
        "minimum",
        "maximum",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "anyOf",
    }
)
_REFUSALS = frozenset(
    {
        "SAFETY",
        "RECITATION",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
    }
)


@dataclass(frozen=True)
class GeminiSettings:
    max_retries: int = 3
    timeout_s: float = 600.0
    tool_mode: ToolMode = "AUTO"


def thinking_config(model: str, effort: str) -> types.ThinkingConfig:
    """low and medium map one to one; high, xhigh and max become the model's highest level."""
    if model.startswith("gemini-2."):
        return types.ThinkingConfig(thinking_budget=_BUDGETS_2_5.get(effort, -1))
    return types.ThinkingConfig(thinking_level=_LEVELS.get(effort, types.ThinkingLevel.HIGH))


def gemini_schema(schema: Any) -> Any:
    """Keep only JSON Schema keywords Gemini accepts. `const` becomes a one-value `enum`."""
    if isinstance(schema, list):
        return [gemini_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "const":
            out["enum"] = [value]
        elif key == "properties" and isinstance(value, dict):
            out[key] = {name: gemini_schema(sub) for name, sub in value.items()}
        elif key in _SCHEMA_KEYS:
            out[key] = gemini_schema(value)
    return out


def part_to_dict(part: types.Part) -> dict[str, Any]:
    """Lossless JSON form of a response part (bytes such as thought signatures become base64)."""
    return part.model_dump(mode="json", exclude_none=True)


def part_from_dict(data: dict[str, Any]) -> types.Part:
    return types.Part.model_validate_json(json.dumps(data))


def _response_payload(part: ToolResultPart) -> dict[str, Any]:
    try:
        value = json.loads(part.content)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict):
        return value
    return {"error": part.content} if part.is_error else {"output": part.content}


def to_gemini_contents(messages: tuple[Message, ...]) -> list[types.ContentUnion]:
    contents: list[types.ContentUnion] = []
    calls: dict[
        str, tuple[str, str | None]
    ] = {}  # neutral call id -> (function name, Gemini's call id)
    for message in messages:
        if isinstance(message, AssistantMessage):
            raw_calls = [
                dict(part["function_call"]) for part in message.raw if "function_call" in part
            ]
            for call, raw in zip(message.tool_calls, raw_calls, strict=False):
                calls[call.id] = (str(raw.get("name") or call.name), raw.get("id"))
            parts = [part_from_dict(dict(part)) for part in message.raw] or [
                types.Part(text=EMPTY_TURN_TEXT)
            ]
            contents.append(types.Content(role="model", parts=parts))
            continue
        user_parts: list[types.Part] = []
        for part in message.parts:
            if isinstance(part, ToolResultPart):
                name, call_id = calls.get(part.tool_call_id, ("unknown_tool", None))
                response = types.FunctionResponse(
                    id=call_id, name=name, response=_response_payload(part)
                )
                user_parts.append(types.Part(function_response=response))
        for part in message.parts:
            if isinstance(part, UserText):
                user_parts.append(types.Part(text=part.text))
        contents.append(types.Content(role="user", parts=user_parts))
    return contents


def build_config(request: LLMRequest, settings: GeminiSettings) -> types.GenerateContentConfig:
    declarations = [
        types.FunctionDeclaration(
            name=spec.name,
            description=spec.description,
            parameters_json_schema=gemini_schema(spec.input_schema),
        )
        for spec in request.tools
    ]
    return types.GenerateContentConfig(
        system_instruction=request.system,
        max_output_tokens=request.max_tokens,
        thinking_config=thinking_config(request.model, request.effort),
        tools=[types.Tool(function_declarations=declarations)] if declarations else None,
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode(settings.tool_mode)
            )
        )
        if declarations
        else None,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )


def parse_response(
    response: types.GenerateContentResponse, request: LLMRequest, *, latency_s: float
) -> LLMResponse:
    candidate = response.candidates[0] if response.candidates else None
    content = candidate.content if candidate is not None else None
    parts = list(content.parts or []) if content is not None else []
    # Older models may omit call ids; derive stable ones from the transcript so replays stay deterministic.
    digest = hashlib.sha1(
        f"{request.agent_name}|{canonical_json(messages_to_jsonable(request.messages))}".encode()
    ).hexdigest()[:12]
    tool_calls: list[ToolCall] = []
    for part in parts:
        call = part.function_call
        if call is None:
            continue
        call_id = call.id or f"gm-{digest}-{len(tool_calls)}"
        tool_calls.append(
            ToolCall(id=call_id, name=call.name or "", arguments=dict(call.args or {}))
        )
    text = "".join(part.text for part in parts if part.text and not part.thought)
    finish = (
        candidate.finish_reason.value
        if candidate is not None and candidate.finish_reason is not None
        else ""
    )
    if candidate is None:
        blocked = (
            response.prompt_feedback is not None
            and response.prompt_feedback.block_reason is not None
        )
        stop_reason = "refusal" if blocked else "end_turn"
    elif finish in _REFUSALS:
        stop_reason = "refusal"
    elif finish == "MAX_TOKENS":
        stop_reason = "max_tokens"
    else:
        stop_reason = "tool_use" if tool_calls else "end_turn"
    meta = response.usage_metadata or types.GenerateContentResponseUsageMetadata()
    cached = meta.cached_content_token_count or 0
    usage = Usage(
        input_tokens=max(0, (meta.prompt_token_count or 0) - cached)
        + (meta.tool_use_prompt_token_count or 0),
        output_tokens=(meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
        cache_read_tokens=cached,
        cache_write_tokens=0,
    )
    return LLMResponse(
        raw=tuple(part_to_dict(part) for part in parts),
        text=text,
        tool_calls=tuple(tool_calls),
        stop_reason=stop_reason,
        usage=usage,
        model=(response.model_version or request.model).removeprefix("models/"),
        latency_s=latency_s,
    )


class GeminiClient:
    def __init__(self, settings: GeminiSettings | None = None, client: Any | None = None) -> None:
        self._settings = settings or GeminiSettings()
        self._client = client or genai.Client(
            http_options=types.HttpOptions(
                timeout=int(self._settings.timeout_s * 1000),
                retry_options=types.HttpRetryOptions(attempts=self._settings.max_retries + 1),
            )
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        contents = to_gemini_contents(request.messages)
        config = build_config(request, self._settings)
        started = time.perf_counter()
        try:
            response = await self._client.aio.models.generate_content(
                model=request.model, contents=contents, config=config
            )
        except errors.APIError as exc:
            retryable = exc.code in (408, 429) or exc.code >= 500
            raise LLMError(
                f"Gemini API error {exc.code} {exc.status}: {exc.message}", retryable=retryable
            ) from exc
        except Exception as exc:  # transport errors left over after the SDK's own retries
            raise LLMError(
                f"Gemini request failed: {type(exc).__name__}: {exc}", retryable=True
            ) from exc
        return parse_response(response, request, latency_s=time.perf_counter() - started)
