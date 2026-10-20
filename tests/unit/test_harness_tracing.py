import json
from pathlib import Path

from investigator.harness.hooks import Hook, HookRunner, ToolCallRecord
from investigator.harness.llm import LLMRequest, LLMResponse, ToolCall, Usage
from investigator.harness.tracing import RunMetrics, Tracer


def _request(agent: str = "observability") -> LLMRequest:
    return LLMRequest(agent, "m", "system", (), (), 100, "medium")


def _response(model: str = "m", usage: Usage | None = None) -> LLMResponse:
    return LLMResponse((), "", (), "end_turn", usage or Usage(10, 5, 100, 0), model)


async def test_tracer_writes_jsonl_and_metrics(tmp_path: Path) -> None:
    path = tmp_path / "run" / "trace.jsonl"
    tracer = Tracer(path, "run-1", clock=lambda: 1.0)
    runner = HookRunner([tracer])
    tracer.emit("run_start", system="multi_agent")
    await runner.after_llm(_request(), _response(), task_id="R1-T1")
    await runner.after_llm(_request("critic"), _response("other", Usage(1, 1, 0, 0)), task_id=None)
    await runner.on_llm_error(_request(), RuntimeError("boom"), task_id="R1-T1")
    call = ToolCall("t1", "query_logs", {"service": "x"})
    ok = ToolCallRecord(
        tool="query_logs", arguments={"service": "x"}, ok=True, attempts=2, evidence_ids=["log-1"]
    )
    failed = ToolCallRecord(
        tool="query_logs", arguments={"service": "y"}, ok=False, error_type="timeout", attempts=3
    )
    await runner.after_tool(call, ok, agent="observability", task_id="R1-T1")
    await runner.after_tool(call, failed, agent="observability", task_id="R1-T1")
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [event["type"] for event in lines] == [
        "run_start",
        "llm_call",
        "llm_call",
        "llm_error",
        "tool_call",
        "tool_call",
    ]
    assert lines[1]["usage"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 100,
        "cache_write_tokens": 0,
    }
    assert all(event["run_id"] == "run-1" for event in lines)
    metrics = RunMetrics.from_events(tracer.events, wall_s=12.3456)
    assert metrics.llm_calls == 2 and metrics.llm_errors == 1
    assert metrics.tool_calls == 2 and metrics.tool_attempts == 5 and metrics.tool_errors == 1
    assert metrics.usage == Usage(11, 6, 100, 0)
    assert metrics.served_models == ["m", "other"]
    assert metrics.per_agent["observability"]["tool_calls"] == 2
    assert "-" not in metrics.per_agent
    data = metrics.to_dict()
    assert data["total_tokens"] == 117 and data["wall_s"] == 12.346


async def test_base_hook_methods_are_no_ops() -> None:
    hook = Hook()
    await hook.before_llm(_request(), task_id=None)
    await hook.after_llm(_request(), _response(), task_id=None)
    await hook.before_tool(ToolCall("t", "x", {}), agent="a", task_id=None)


def test_tracer_without_path_keeps_events_in_memory() -> None:
    tracer = Tracer(None, "run-2")
    tracer.emit("phase", name="planning")
    assert tracer.events[0]["name"] == "planning"
