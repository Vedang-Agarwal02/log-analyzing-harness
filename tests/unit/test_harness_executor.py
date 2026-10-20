import asyncio
import json
import time

from pydantic import BaseModel

from investigator.callctx import CURRENT_CALLER
from investigator.domain.evidence import Evidence, EvidenceStore
from investigator.errors import NotFound, OperationTimeout
from investigator.harness.budget import Budget, BudgetLimits
from investigator.harness.executor import ExecutorConfig, ToolExecutor
from investigator.harness.hooks import HookRunner
from investigator.harness.llm import ToolCall
from investigator.harness.retry import RetryPolicy
from investigator.harness.tools import ToolContext, ToolOutput, ToolRegistry, tool
from investigator.harness.tracing import Tracer


class ServiceInput(BaseModel):
    service: str


class Counter:
    calls = 0


@tool(name="good", description="Always works.", input_model=ServiceInput)
async def good(args: ServiceInput, ctx: ToolContext) -> ToolOutput:
    ev = Evidence(
        evidence_id=f"met-{args.service}",
        kind="metric_series",
        service=args.service,
        summary="s",
        source_tool="good",
    )
    return ToolOutput(data={"service": args.service, "caller": CURRENT_CALLER.get()}, evidence=[ev])


@tool(name="flaky", description="Times out once.", input_model=ServiceInput)
async def flaky(args: ServiceInput, ctx: ToolContext) -> ToolOutput:
    Counter.calls += 1
    if Counter.calls == 1:
        raise OperationTimeout("slow backend")
    return ToolOutput(data={"ok": True})


@tool(name="down", description="Always times out.", input_model=ServiceInput)
async def down(args: ServiceInput, ctx: ToolContext) -> ToolOutput:
    raise OperationTimeout("still down", hint="use metrics instead")


@tool(name="missing", description="Not found.", input_model=ServiceInput)
async def missing(args: ServiceInput, ctx: ToolContext) -> ToolOutput:
    raise NotFound(f"unknown service {args.service}", hint="Call list_services.")


@tool(name="buggy", description="Raises a bug.", input_model=ServiceInput)
async def buggy(args: ServiceInput, ctx: ToolContext) -> ToolOutput:
    raise KeyError("oops")


@tool(name="sleepy", description="Sleeps.", input_model=ServiceInput, timeout_s=0.05)
async def sleepy(args: ServiceInput, ctx: ToolContext) -> ToolOutput:
    await asyncio.sleep(1.0)
    return ToolOutput()


@tool(name="nap", description="Sleeps briefly.", input_model=ServiceInput)
async def nap(args: ServiceInput, ctx: ToolContext) -> ToolOutput:
    await asyncio.sleep(0.1)
    return ToolOutput(data={"service": args.service})


def _executor(max_tool_calls: int = 100, tracer: Tracer | None = None) -> ToolExecutor:
    registry = ToolRegistry([good, flaky, down, missing, buggy, sleepy, nap])
    config = ExecutorConfig(
        retry=RetryPolicy(base_delay_s=0.0), breaker_threshold=2, max_rate_limit_wait_s=0.0
    )
    budget = Budget(BudgetLimits(max_tool_calls=max_tool_calls))
    return ToolExecutor(
        registry, budget=budget, hooks=HookRunner([tracer] if tracer else []), config=config
    )


def _ctx() -> ToolContext:
    return ToolContext(caller="R1-T1", evidence=EvidenceStore())


async def _run(
    executor: ToolExecutor, name: str, args: dict[str, object], ctx: ToolContext | None = None
) -> dict:  # type: ignore[type-arg]
    part = await executor.execute(
        ToolCall("call-1", name, args), ctx or _ctx(), agent="observability", task_id="R1-T1"
    )
    envelope = json.loads(part.content)
    assert part.is_error == (not envelope["ok"])
    return envelope  # type: ignore[no-any-return]


async def test_success_registers_evidence_and_sets_caller() -> None:
    ctx = _ctx()
    envelope = await _run(_executor(), "good", {"service": "orders-service"}, ctx)
    assert envelope["ok"] and envelope["attempts"] == 1
    assert envelope["evidence_ids"] == ["met-orders-service"]
    assert envelope["data"]["caller"] == "R1-T1"
    stored = ctx.evidence.get("met-orders-service")
    assert stored is not None and stored.source_call_id == "call-1"
    assert CURRENT_CALLER.get() == "anonymous"


async def test_unknown_tool_and_invalid_arguments() -> None:
    executor = _executor()
    unknown = await _run(executor, "nope", {})
    assert unknown["error"]["type"] == "unknown_tool" and "good" in unknown["error"]["message"]
    invalid = await _run(executor, "good", {"service": 3})
    assert (
        invalid["error"]["type"] == "invalid_arguments" and "service" in invalid["error"]["message"]
    )


async def test_retryable_error_is_retried_then_succeeds() -> None:
    Counter.calls = 0
    envelope = await _run(_executor(), "flaky", {"service": "x"})
    assert envelope["ok"] and envelope["attempts"] == 2


async def test_non_retryable_error_is_not_retried() -> None:
    envelope = await _run(_executor(), "missing", {"service": "ghost"})
    assert envelope["attempts"] == 1
    assert envelope["error"] == {
        "type": "not_found",
        "message": "unknown service ghost",
        "retryable": False,
        "hint": "Call list_services.",
    }


async def test_persistent_failure_opens_circuit_breaker() -> None:
    executor = _executor()
    first = await _run(executor, "down", {"service": "x"})
    assert first["attempts"] == 3 and first["error"]["type"] == "timeout"
    assert first["error"]["hint"] == "use metrics instead"
    await _run(executor, "down", {"service": "x"})
    third = await _run(executor, "down", {"service": "x"})
    assert third["error"]["type"] == "circuit_open" and third["attempts"] == 0
    other_target = await _run(executor, "down", {"service": "y"})
    assert other_target["error"]["type"] == "timeout"


async def test_bug_in_tool_becomes_internal_error() -> None:
    envelope = await _run(_executor(), "buggy", {"service": "x"})
    assert envelope["error"]["type"] == "internal" and envelope["attempts"] == 1


async def test_asyncio_timeout_is_reported_as_timeout() -> None:
    envelope = await _run(_executor(), "sleepy", {"service": "x"})
    assert envelope["error"]["type"] == "timeout" and envelope["attempts"] == 3


async def test_tool_call_budget_is_enforced() -> None:
    executor = _executor(max_tool_calls=1)
    assert (await _run(executor, "good", {"service": "a"}))["ok"]
    blocked = await _run(executor, "good", {"service": "b"})
    assert blocked["error"]["type"] == "budget_exhausted"


async def test_execute_all_runs_in_parallel_and_keeps_order() -> None:
    tracer = Tracer(None, "run")
    executor = _executor(tracer=tracer)
    calls = [ToolCall(f"c{i}", "nap", {"service": f"s{i}"}) for i in range(3)]
    started = time.perf_counter()
    parts = await executor.execute_all(calls, _ctx(), agent="observability", task_id="R1-T1")
    assert time.perf_counter() - started < 0.25
    assert [p.tool_call_id for p in parts] == ["c0", "c1", "c2"]
    assert [e["tool"] for e in tracer.events if e["type"] == "tool_call"] == ["nap", "nap", "nap"]
