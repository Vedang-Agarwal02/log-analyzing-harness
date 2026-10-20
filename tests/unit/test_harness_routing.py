import pytest

from investigator.harness.llm import LLMError, LLMRequest, LLMResponse, Usage, UserMessage, UserText
from investigator.harness.routing import RoutingLLM, provider_for


class EchoClient:
    def __init__(self, name: str) -> None:
        self.name = name

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            raw=(),
            text=self.name,
            tool_calls=(),
            stop_reason="end_turn",
            usage=Usage(),
            model=request.model,
        )


def _request(model: str) -> LLMRequest:
    return LLMRequest(
        agent_name="planner",
        model=model,
        system="s",
        messages=(UserMessage(parts=(UserText("hi"),)),),
        tools=(),
        max_tokens=100,
        effort="medium",
    )


def test_provider_for_uses_the_model_id_prefix() -> None:
    assert provider_for("claude-opus-5-5") == "anthropic"
    assert provider_for("gemini-4-argon") == "gemini"
    with pytest.raises(ValueError, match="no provider for model 'gpt-5'"):
        provider_for("gpt-5")


async def test_router_creates_each_client_once_on_first_use() -> None:
    created: list[str] = []

    def factory(name: str) -> EchoClient:
        created.append(name)
        return EchoClient(name)

    router = RoutingLLM(
        {"anthropic": lambda: factory("anthropic"), "gemini": lambda: factory("gemini")}
    )
    assert created == []
    assert (await router.complete(_request("gemini-4-argon"))).text == "gemini"
    assert (await router.complete(_request("gemini-3.8-flash"))).text == "gemini"
    assert (await router.complete(_request("claude-opus-5-5"))).text == "anthropic"
    assert created == ["gemini", "anthropic"]


async def test_router_reports_unknown_models_and_broken_factories_as_llm_errors() -> None:
    def broken() -> EchoClient:
        raise ValueError("Missing key inputs argument!")

    router = RoutingLLM({"gemini": broken})
    with pytest.raises(LLMError, match="no provider for model"):
        await router.complete(_request("gpt-5"))
    with pytest.raises(LLMError, match="could not create the gemini client: Missing key"):
        await router.complete(_request("gemini-4-argon"))
    with pytest.raises(LLMError, match="no client configured for provider 'anthropic'"):
        await router.complete(_request("claude-opus-5-5"))
