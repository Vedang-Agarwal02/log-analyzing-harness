"""The agent loop: the model and its tools in a loop until the agent submits typed output."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError

from investigator.harness.budget import Budget, BudgetExceeded
from investigator.harness.executor import ToolExecutor, error_envelope
from investigator.harness.hooks import HookRunner
from investigator.harness.llm import (
    LLMClient,
    LLMError,
    LLMRequest,
    Message,
    ToolCall,
    ToolResultPart,
    ToolSpec,
    Usage,
    UserMessage,
    UserText,
    assistant_message,
)
from investigator.harness.schema import format_validation_error, model_input_schema
from investigator.harness.tools import ToolContext
from investigator.jsonutil import canonical_json

OutputValidator = Callable[[BaseModel], list[str]]

NUDGE_TEXT = (
    "You ended your turn without calling `{tool}`. Call `{tool}` now with your final result. "
    "Do not answer in plain text."
)
TRUNCATED_TEXT = (
    "Your previous response hit the output token limit, so its tool calls were not executed. "
    "Re-issue them with more compact arguments."
)
TOOL_LIMIT_TEXT = (
    "You have used all {limit} tool calls allowed for this task. Call `{tool}` now with what you have, "
    "and list anything you could not check as a data gap."
)


@dataclass(frozen=True)
class OutputSpec:
    tool_name: str
    description: str
    model: type[BaseModel]

    def tool_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.tool_name,
            description=self.description,
            input_schema=model_input_schema(self.model),
        )


@dataclass(frozen=True)
class AgentSpec:
    name: str
    system_prompt: str
    model: str
    effort: str
    tools: tuple[str, ...]
    output: OutputSpec
    max_turns: int = 12
    max_tool_calls: int = 16
    max_tokens: int = 32000
    max_nudges: int = 2
    use_reserve: bool = False


@dataclass
class AgentResult:
    agent: str
    status: Literal["ok", "failed"]
    output: BaseModel | None
    error: str | None
    turns: int
    tool_calls: int
    usage: Usage
    messages: list[Message]


def _error_part(call: ToolCall, error_type: str, message: str) -> ToolResultPart:
    envelope = error_envelope(error_type, message, retryable=False)
    return ToolResultPart(tool_call_id=call.id, content=canonical_json(envelope), is_error=True)


class Agent:
    def __init__(
        self,
        spec: AgentSpec,
        *,
        llm: LLMClient,
        executor: ToolExecutor,
        budget: Budget,
        hooks: HookRunner,
    ) -> None:
        self.spec = spec
        self._llm = llm
        self._executor = executor
        self._budget = budget
        self._hooks = hooks
        available = executor.registry.subset(spec.tools)
        specs = [*available.specs(), spec.output.tool_spec()]
        self._tool_specs: tuple[ToolSpec, ...] = tuple(sorted(specs, key=lambda item: item.name))

    async def run(
        self,
        user_message: str,
        ctx: ToolContext,
        *,
        validator: OutputValidator | None = None,
        task_id: str | None = None,
    ) -> AgentResult:
        spec = self.spec
        submit = spec.output.tool_name
        messages: list[Message] = [UserMessage(parts=(UserText(user_message),))]
        usage = Usage()
        turns = 0
        tool_calls_used = 0
        nudges = 0

        def finish(
            status: Literal["ok", "failed"],
            output: BaseModel | None = None,
            error: str | None = None,
        ) -> AgentResult:
            return AgentResult(
                spec.name, status, output, error, turns, tool_calls_used, usage, messages
            )

        while turns < spec.max_turns:
            try:
                self._budget.check(use_reserve=spec.use_reserve)
            except BudgetExceeded as exc:
                return finish("failed", error=str(exc))
            request = LLMRequest(
                agent_name=spec.name,
                model=spec.model,
                system=spec.system_prompt,
                messages=tuple(messages),
                tools=self._tool_specs,
                max_tokens=spec.max_tokens,
                effort=spec.effort,
            )
            await self._hooks.before_llm(request, task_id=task_id)
            try:
                response = await self._llm.complete(request)
            except LLMError as exc:
                await self._hooks.on_llm_error(request, exc, task_id=task_id)
                return finish("failed", error=f"llm_error: {exc}")
            turns += 1
            usage = usage + response.usage
            self._budget.record_llm_call(response.usage)
            await self._hooks.after_llm(request, response, task_id=task_id)
            messages.append(assistant_message(response))

            if response.stop_reason == "refusal":
                return finish("failed", error="model_refusal")
            if response.stop_reason == "max_tokens" and response.tool_calls:
                parts = tuple(
                    _error_part(call, "truncated_turn", "Not executed: the response was cut off.")
                    for call in response.tool_calls
                )
                messages.append(UserMessage(parts=(*parts, UserText(TRUNCATED_TEXT))))
                continue
            if not response.tool_calls:
                if response.stop_reason == "pause_turn":
                    continue
                if nudges >= spec.max_nudges:
                    return finish("failed", error=f"no_output: the agent never called {submit}")
                nudges += 1
                messages.append(UserMessage(parts=(UserText(NUDGE_TEXT.format(tool=submit)),)))
                continue

            results: dict[str, ToolResultPart] = {}
            data_calls = [call for call in response.tool_calls if call.name != submit]
            allowed = max(0, spec.max_tool_calls - tool_calls_used)
            runnable, refused = data_calls[:allowed], data_calls[allowed:]
            for call in refused:
                results[call.id] = _error_part(
                    call,
                    "tool_call_limit",
                    f"Tool-call limit ({spec.max_tool_calls}) reached; call {submit} now.",
                )
            tool_calls_used += len(runnable)
            for part in await self._executor.execute_all(
                runnable, ctx, agent=spec.name, task_id=task_id
            ):
                results[part.tool_call_id] = part

            accepted: BaseModel | None = None
            for call in response.tool_calls:
                if call.name != submit:
                    continue
                if accepted is not None:
                    results[call.id] = _error_part(
                        call, "duplicate_submission", "Only one submission is accepted."
                    )
                    continue
                part, parsed = self._check_output(call, validator)
                results[call.id] = part
                accepted = parsed
            if accepted is not None:
                return finish("ok", output=accepted)

            extra: tuple[UserText, ...] = ()
            if tool_calls_used >= spec.max_tool_calls:
                extra = (UserText(TOOL_LIMIT_TEXT.format(limit=spec.max_tool_calls, tool=submit)),)
            ordered = tuple(results[call.id] for call in response.tool_calls)
            messages.append(UserMessage(parts=(*ordered, *extra)))
        return finish("failed", error=f"max_turns: no valid {submit} within {spec.max_turns} turns")

    def _check_output(
        self, call: ToolCall, validator: OutputValidator | None
    ) -> tuple[ToolResultPart, BaseModel | None]:
        try:
            parsed = self.spec.output.model.model_validate(call.arguments)
        except ValidationError as exc:
            message = format_validation_error(exc) + ". Fix these fields and call the tool again."
            return _error_part(call, "invalid_output", message), None
        problems = validator(parsed) if validator is not None else []
        if problems:
            message = "; ".join(problems[:12]) + ". Fix these problems and call the tool again."
            return _error_part(call, "invalid_output", message), None
        accepted = ToolResultPart(
            tool_call_id=call.id, content=canonical_json({"ok": True, "message": "accepted"})
        )
        return accepted, parsed
