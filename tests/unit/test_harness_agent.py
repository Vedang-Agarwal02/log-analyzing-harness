import dataclasses

import pytest
from pydantic import BaseModel, Field

from investigator.domain.evidence import Evidence, EvidenceStore
from investigator.harness.agent import Agent, AgentSpec, OutputSpec
from investigator.harness.budget import Budget, BudgetLimits
from investigator.harness.executor import ExecutorConfig, ToolExecutor
from investigator.harness.hooks import HookRunner
from investigator.harness.llm import LLMError, LLMRequest, ToolResultPart, UserMessage, UserText
from investigator.harness.retry import RetryPolicy
from investigator.harness.scripted import (
    ScriptedLLM,
    result_evidence_ids,
    sequence,
    text_response,
    tool_call_response,
)
from investigator.harness.tools import ToolContext, ToolOutput, ToolRegistry, tool


class LookupInput(BaseModel):
    key: str = "x"


class Answer(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class Calls:
    count = 0


@tool(name="lookup", description="Look something up.", input_model=LookupInput)
async def lookup(args: LookupInput, ctx: ToolContext) -> ToolOutput:
    Calls.count += 1
    ev = Evidence(
        evidence_id=f"met-{args.key}", kind="metric_series", summary="s", source_tool="lookup"
    )
    return ToolOutput(data={"key": args.key}, evidence=[ev])


OUTPUT = OutputSpec(tool_name="submit_answer", description="Submit the answer.", model=Answer)


def _agent(llm: ScriptedLLM, **overrides: object) -> tuple[Agent, EvidenceStore]:
    spec = AgentSpec(
        name="tester",
        system_prompt="You test.",
        model="scripted",
        effort="medium",
        tools=("lookup",),
        output=OUTPUT,
        max_turns=int(overrides.get("max_turns", 6)),  # type: ignore[call-overload]
        max_tool_calls=int(overrides.get("max_tool_calls", 5)),  # type: ignore[call-overload]
    )
    limits = overrides.get("limits", BudgetLimits())
    budget = Budget(limits)  # type: ignore[arg-type]
    registry = ToolRegistry([lookup])
    executor = ToolExecutor(
        registry,
        budget=budget,
        hooks=HookRunner(),
        config=ExecutorConfig(retry=RetryPolicy(base_delay_s=0.0)),
    )
    return Agent(
        spec, llm=llm, executor=executor, budget=budget, hooks=HookRunner()
    ), EvidenceStore()


def _validator(store: EvidenceStore):  # type: ignore[no-untyped-def]
    def validate(model: BaseModel) -> list[str]:
        assert isinstance(model, Answer)
        missing = store.missing(model.evidence_ids)
        return [f"unknown evidence id(s) {', '.join(missing)}"] if missing else []

    return validate


def _submit(**arguments: object):  # type: ignore[no-untyped-def]
    return tool_call_response(("submit_answer", dict(arguments)))


def _last_user_message(request: LLMRequest) -> UserMessage:
    last = request.messages[-1]
    assert isinstance(last, UserMessage)
    return last


async def test_agent_calls_tool_then_submits_with_cited_evidence() -> None:
    llm = ScriptedLLM(
        {
            "tester": sequence(
                tool_call_response(("lookup", {"key": "x"})),
                lambda req: _submit(text="done", evidence_ids=result_evidence_ids(req)),
            )
        }
    )
    agent, store = _agent(llm)
    result = await agent.run(
        "go", ToolContext(caller="t", evidence=store), validator=_validator(store)
    )
    assert result.status == "ok" and isinstance(result.output, Answer)
    assert result.output.evidence_ids == ["met-x"]
    assert (result.turns, result.tool_calls) == (2, 1)
    names = [spec.name for spec in llm.calls[0].tools]
    assert names == ["lookup", "submit_answer"]


async def test_schema_and_validator_errors_are_fed_back() -> None:
    llm = ScriptedLLM(
        {
            "tester": sequence(
                _submit(evidence_ids=["met-x"]),
                _submit(text="t", evidence_ids=["met-nope"]),
                tool_call_response(("lookup", {"key": "x"})),
                _submit(text="t", evidence_ids=["met-x"]),
            )
        }
    )
    agent, store = _agent(llm)
    result = await agent.run(
        "go", ToolContext(caller="t", evidence=store), validator=_validator(store)
    )
    assert result.status == "ok" and result.turns == 4
    schema_error = _last_user_message(llm.calls[1]).parts[0]
    assert isinstance(schema_error, ToolResultPart) and schema_error.is_error
    assert "invalid_output" in schema_error.content and "text" in schema_error.content
    validator_error = _last_user_message(llm.calls[2]).parts[0]
    assert isinstance(validator_error, ToolResultPart) and "met-nope" in validator_error.content


async def test_nudges_when_model_answers_in_text() -> None:
    llm = ScriptedLLM({"tester": sequence(text_response("I think..."), _submit(text="ok"))})
    agent, store = _agent(llm)
    result = await agent.run("go", ToolContext(caller="t", evidence=store))
    assert result.status == "ok"
    nudge = _last_user_message(llm.calls[1]).parts[0]
    assert isinstance(nudge, UserText) and "submit_answer" in nudge.text


async def test_gives_up_after_max_nudges() -> None:
    llm = ScriptedLLM({"tester": sequence(text_response("never"))})
    agent, store = _agent(llm)
    result = await agent.run("go", ToolContext(caller="t", evidence=store))
    assert (
        result.status == "failed"
        and result.error is not None
        and result.error.startswith("no_output")
    )
    assert result.turns == 3


async def test_max_turns_failure() -> None:
    llm = ScriptedLLM({"tester": sequence(tool_call_response(("lookup", {})))})
    agent, store = _agent(llm, max_turns=3, max_tool_calls=50)
    result = await agent.run("go", ToolContext(caller="t", evidence=store))
    assert (
        result.status == "failed"
        and result.error is not None
        and result.error.startswith("max_turns")
    )


async def test_refusal_fails_the_run() -> None:
    llm = ScriptedLLM({"tester": sequence(text_response("", stop_reason="refusal"))})
    agent, store = _agent(llm)
    result = await agent.run("go", ToolContext(caller="t", evidence=store))
    assert (result.status, result.error) == ("failed", "model_refusal")


async def test_truncated_turn_does_not_execute_tools() -> None:
    Calls.count = 0
    truncated = dataclasses.replace(tool_call_response(("lookup", {})), stop_reason="max_tokens")
    llm = ScriptedLLM({"tester": sequence(truncated, _submit(text="ok"))})
    agent, store = _agent(llm)
    result = await agent.run("go", ToolContext(caller="t", evidence=store))
    assert result.status == "ok" and Calls.count == 0
    parts = _last_user_message(llm.calls[1]).parts
    assert isinstance(parts[0], ToolResultPart) and "truncated_turn" in parts[0].content
    assert isinstance(parts[-1], UserText)


async def test_tool_call_limit_refuses_extra_calls() -> None:
    Calls.count = 0
    llm = ScriptedLLM(
        {
            "tester": sequence(
                tool_call_response(("lookup", {"key": "a"}), ("lookup", {"key": "b"})),
                _submit(text="ok"),
            )
        }
    )
    agent, store = _agent(llm, max_tool_calls=1)
    result = await agent.run("go", ToolContext(caller="t", evidence=store))
    assert result.status == "ok" and Calls.count == 1 and result.tool_calls == 1
    parts = _last_user_message(llm.calls[1]).parts
    assert len(parts) == 3
    assert isinstance(parts[1], ToolResultPart) and "tool_call_limit" in parts[1].content
    assert isinstance(parts[2], UserText) and "submit_answer" in parts[2].text


async def test_budget_is_checked_before_each_call() -> None:
    llm = ScriptedLLM({"tester": sequence(tool_call_response(("lookup", {})))})
    limits = BudgetLimits(max_llm_calls=1, reserve_llm_calls=0)
    agent, store = _agent(llm, limits=limits)
    result = await agent.run("go", ToolContext(caller="t", evidence=store))
    assert result.status == "failed" and "budget exhausted" in (result.error or "")
    assert result.turns == 1


async def test_llm_error_fails_the_run() -> None:
    def boom(request: LLMRequest):  # type: ignore[no-untyped-def]
        raise LLMError("service down", retryable=True)

    agent, store = _agent(ScriptedLLM({"tester": boom}))
    result = await agent.run("go", ToolContext(caller="t", evidence=store))
    assert result.status == "failed" and "service down" in (result.error or "")


async def test_parallel_tool_results_return_in_one_message() -> None:
    llm = ScriptedLLM(
        {
            "tester": sequence(
                tool_call_response(("lookup", {"key": "a"}), ("lookup", {"key": "b"})),
                _submit(text="ok"),
            )
        }
    )
    agent, store = _agent(llm)
    await agent.run("go", ToolContext(caller="t", evidence=store))
    parts = _last_user_message(llm.calls[1]).parts
    assert [type(p).__name__ for p in parts] == ["ToolResultPart", "ToolResultPart"]
    assert store.has("met-a") and store.has("met-b")


def test_unknown_tool_in_spec_fails_fast() -> None:
    spec = AgentSpec("x", "s", "m", "medium", ("nope",), OUTPUT)
    budget = Budget(BudgetLimits())
    executor = ToolExecutor(ToolRegistry([lookup]), budget=budget, hooks=HookRunner())
    with pytest.raises(KeyError):
        Agent(spec, llm=ScriptedLLM({}), executor=executor, budget=budget, hooks=HookRunner())
