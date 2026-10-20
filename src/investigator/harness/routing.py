"""Send each request to the client for its model's provider, chosen from the model id prefix.

Clients are created on first use, so a run only needs API keys for the providers it actually calls,
and agents in `config/agents.yaml` can use models from different providers in the same run.
"""

from collections.abc import Callable, Mapping

from investigator.harness.llm import LLMClient, LLMError, LLMRequest, LLMResponse

PROVIDER_PREFIXES: dict[str, str] = {"claude-": "anthropic", "gemini-": "gemini"}


def provider_for(model: str) -> str:
    for prefix, provider in PROVIDER_PREFIXES.items():
        if model.startswith(prefix):
            return provider
    known = ", ".join(f"{prefix}*" for prefix in PROVIDER_PREFIXES)
    raise ValueError(f"no provider for model {model!r}; model ids must look like {known}")


class RoutingLLM:
    def __init__(self, factories: Mapping[str, Callable[[], LLMClient]]) -> None:
        self._factories = dict(factories)
        self._clients: dict[str, LLMClient] = {}

    def client_for(self, model: str) -> LLMClient:
        try:
            provider = provider_for(model)
        except ValueError as exc:
            raise LLMError(str(exc), retryable=False) from exc
        client = self._clients.get(provider)
        if client is None:
            factory = self._factories.get(provider)
            if factory is None:
                raise LLMError(f"no client configured for provider {provider!r}", retryable=False)
            try:
                client = factory()
            except Exception as exc:  # most often a missing API key
                raise LLMError(
                    f"could not create the {provider} client: {exc}", retryable=False
                ) from exc
            self._clients[provider] = client
        return client

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return await self.client_for(request.model).complete(request)
