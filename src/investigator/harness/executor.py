"""Executes tool calls with validation, budget, circuit breaker, timeout and retries.

`execute` never raises. Every outcome becomes a JSON result envelope the model can act on.
"""

import asyncio
import dataclasses
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from investigator.callctx import CURRENT_CALLER
from investigator.errors import OperationError
from investigator.harness.budget import Budget, BudgetExceeded
from investigator.harness.hooks import HookRunner, ToolCallRecord
from investigator.harness.llm import ToolCall, ToolResultPart
from investigator.harness.retry import CircuitBreaker, RetryPolicy
from investigator.harness.schema import format_validation_error
from investigator.harness.tools import ToolContext, ToolOutput, ToolRegistry
from investigator.jsonutil import canonical_json

Failure = tuple[str, bool, str, float | None, str | None]


@dataclass(frozen=True)
class ExecutorConfig:
    default_timeout_s: float = 8.0
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    breaker_threshold: int = 4
    breaker_cooldown_s: float = 30.0
    max_rate_limit_wait_s: float = 2.0


def error_envelope(
    error_type: str, message: str, *, retryable: bool, hint: str | None = None, attempts: int = 0
) -> dict[str, Any]:
    error: dict[str, Any] = {"type": error_type, "message": message, "retryable": retryable}
    if hint:
        error["hint"] = hint
    return {"ok": False, "error": error, "attempts": attempts}


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        budget: Budget,
        hooks: HookRunner,
        config: ExecutorConfig | None = None,
        seed: int = 0,
    ) -> None:
        self.registry = registry
        self._budget = budget
        self._hooks = hooks
        self._config = config or ExecutorConfig()
        self._breaker = CircuitBreaker(
            self._config.breaker_threshold, self._config.breaker_cooldown_s
        )
        self._rng = random.Random(seed)

    async def execute(
        self, call: ToolCall, ctx: ToolContext, *, agent: str, task_id: str | None
    ) -> ToolResultPart:
        await self._hooks.before_tool(call, agent=agent, task_id=task_id)
        record = ToolCallRecord(tool=call.name, arguments=dict(call.arguments))
        started = time.perf_counter()
        envelope = await self._run(call, dataclasses.replace(ctx, call_id=call.id), record)
        record.latency_s = time.perf_counter() - started
        await self._hooks.after_tool(call, record, agent=agent, task_id=task_id)
        return ToolResultPart(
            tool_call_id=call.id, content=canonical_json(envelope), is_error=not envelope["ok"]
        )

    async def execute_all(
        self, calls: Sequence[ToolCall], ctx: ToolContext, *, agent: str, task_id: str | None
    ) -> list[ToolResultPart]:
        if not calls:
            return []
        results = await asyncio.gather(
            *(self.execute(call, ctx, agent=agent, task_id=task_id) for call in calls)
        )
        return list(results)

    async def _run(
        self, call: ToolCall, ctx: ToolContext, record: ToolCallRecord
    ) -> dict[str, Any]:
        tool = self.registry.get(call.name)
        if tool is None:
            record.error_type = "unknown_tool"
            available = ", ".join(self.registry.names())
            return error_envelope(
                "unknown_tool",
                f"Unknown tool {call.name!r}. Available tools: {available}.",
                retryable=False,
            )
        try:
            arguments = tool.parse_arguments(call.arguments)
        except ValidationError as exc:
            record.error_type = "invalid_arguments"
            return error_envelope(
                "invalid_arguments",
                format_validation_error(exc),
                retryable=False,
                hint="Fix the arguments to match the tool's input schema.",
            )
        try:
            self._budget.check_tool_call()
        except BudgetExceeded as exc:
            record.error_type = "budget_exhausted"
            return error_envelope(
                "budget_exhausted",
                str(exc),
                retryable=False,
                hint="Stop calling tools and submit your result now.",
            )
        self._budget.record_tool_call()
        breaker_key = f"{call.name}:{call.arguments.get('service', '*')}"
        if not self._breaker.allow(breaker_key):
            record.error_type = "circuit_open"
            return error_envelope(
                "circuit_open",
                f"{call.name} has failed repeatedly for this target and is temporarily disabled.",
                retryable=False,
                hint="Do not call it again for this target; record a data gap and use other evidence.",
            )
        timeout = tool.timeout_s or self._config.default_timeout_s
        attempt = 0
        while True:
            attempt += 1
            record.attempts = attempt
            failure: Failure
            token = CURRENT_CALLER.set(ctx.caller)
            try:
                output = await asyncio.wait_for(tool.run(arguments, ctx), timeout=timeout)
            except TimeoutError:
                failure = (
                    "timeout",
                    True,
                    f"{call.name} timed out after {timeout:.1f}s",
                    None,
                    "Retry later, narrow the time window, or use other evidence. Record a data gap if it keeps failing.",
                )
            except OperationError as exc:
                failure = (exc.error_type, exc.retryable, str(exc), exc.retry_after_s, exc.hint)
            except Exception as exc:  # a bug inside a tool must never crash the agent loop
                failure = ("internal", False, f"{type(exc).__name__}: {exc}", None, None)
            else:
                self._breaker.record_success(breaker_key)
                return self._success(output, ctx, record)
            finally:
                CURRENT_CALLER.reset(token)
            error_type, retryable, message, retry_after, hint = failure
            if retryable and attempt < self._config.retry.max_attempts:
                if retry_after is not None:
                    delay = min(retry_after, self._config.max_rate_limit_wait_s)
                else:
                    delay = self._config.retry.delay_for(attempt, self._rng)
                if delay > 0:
                    await asyncio.sleep(delay)
                continue
            if retryable:
                self._breaker.record_failure(breaker_key)
            record.error_type = error_type
            return error_envelope(
                error_type, message, retryable=retryable, hint=hint, attempts=attempt
            )

    def _success(
        self, output: ToolOutput, ctx: ToolContext, record: ToolCallRecord
    ) -> dict[str, Any]:
        evidence_ids: set[str] = set()
        for item in output.evidence:
            stored = (
                item
                if item.source_call_id
                else item.model_copy(update={"source_call_id": ctx.call_id})
            )
            ctx.evidence.add(stored)
            evidence_ids.add(stored.evidence_id)
        record.ok = True
        record.evidence_ids = sorted(evidence_ids)
        record.partial = output.partial
        return {
            "ok": True,
            "data": output.data,
            "evidence_ids": record.evidence_ids,
            "partial": output.partial,
            "warnings": output.warnings,
            "attempts": record.attempts,
        }
