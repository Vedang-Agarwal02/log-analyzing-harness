"""Record real LLM responses to a cassette and replay them offline.

The key is a SHA-256 of the canonical request, so any change to a prompt, tool schema or
transcript is a cache miss. Replay only works because prompts and tool results are deterministic.
"""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from investigator.harness.llm import (
    LLMClient,
    LLMError,
    LLMRequest,
    LLMResponse,
    messages_to_jsonable,
    response_from_dict,
    response_to_dict,
)
from investigator.jsonutil import canonical_json


def request_key(request: LLMRequest) -> str:
    payload = {
        "agent": request.agent_name,
        "model": request.model,
        "system": request.system,
        "tools": [asdict(spec) for spec in request.tools],
        "messages": messages_to_jsonable(request.messages),
        "max_tokens": request.max_tokens,
        "effort": request.effort,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class CassetteMiss(LLMError):
    """A replayed request has no recorded response."""


class RecordingLLM:
    def __init__(
        self, cassette: Path, *, mode: Literal["record", "replay"], inner: LLMClient | None = None
    ) -> None:
        if mode == "record" and inner is None:
            raise ValueError("record mode needs an inner client")
        if mode == "replay" and not cassette.exists():
            raise FileNotFoundError(f"cassette not found: {cassette}")
        self._cassette = cassette
        self._mode = mode
        self._inner = inner
        self._entries: dict[str, dict[str, Any]] = (
            json.loads(cassette.read_text(encoding="utf-8")) if cassette.exists() else {}
        )
        self.hits = 0
        self.misses = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        key = request_key(request)
        if self._mode == "replay":
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                raise CassetteMiss(
                    f"no recorded response for agent {request.agent_name!r} (key {key[:12]}); "
                    "the prompt or transcript changed, so re-record the cassette",
                    retryable=False,
                )
            self.hits += 1
            return response_from_dict(entry)
        assert self._inner is not None
        response = await self._inner.complete(request)
        self._entries[key] = response_to_dict(response)
        self._save()
        return response

    def _save(self) -> None:
        self._cassette.parent.mkdir(parents=True, exist_ok=True)
        self._cassette.write_text(
            json.dumps(self._entries, sort_keys=True, indent=1) + "\n", encoding="utf-8"
        )
