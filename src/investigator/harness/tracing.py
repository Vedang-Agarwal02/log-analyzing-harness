"""JSONL run trace (one event per line) and the efficiency metrics derived from it."""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from investigator.harness.hooks import Hook, ToolCallRecord
from investigator.harness.llm import LLMRequest, LLMResponse, ToolCall, Usage


class Tracer(Hook):
    def __init__(
        self, path: Path | None, run_id: str, clock: Callable[[], float] = time.time
    ) -> None:
        self.path = path
        self.run_id = run_id
        self._clock = clock
        self.events: list[dict[str, Any]] = []
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def emit(self, event_type: str, **fields: Any) -> None:
        event: dict[str, Any] = {
            "ts": round(self._clock(), 3),
            "type": event_type,
            "run_id": self.run_id,
            **fields,
        }
        self.events.append(event)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")

    async def after_llm(
        self, request: LLMRequest, response: LLMResponse, *, task_id: str | None
    ) -> None:
        self.emit(
            "llm_call",
            agent=request.agent_name,
            task_id=task_id,
            model=request.model,
            served_model=response.model,
            stop_reason=response.stop_reason,
            usage=response.usage.to_dict(),
            latency_s=round(response.latency_s, 3),
            fallback_used=response.fallback_used,
            tool_calls=[call.name for call in response.tool_calls],
        )

    async def on_llm_error(
        self, request: LLMRequest, error: Exception, *, task_id: str | None
    ) -> None:
        self.emit(
            "llm_error",
            agent=request.agent_name,
            task_id=task_id,
            model=request.model,
            error=f"{type(error).__name__}: {error}",
        )

    async def after_tool(
        self, call: ToolCall, record: ToolCallRecord, *, agent: str, task_id: str | None
    ) -> None:
        self.emit(
            "tool_call",
            agent=agent,
            task_id=task_id,
            tool=record.tool,
            arguments=record.arguments,
            ok=record.ok,
            error_type=record.error_type,
            attempts=record.attempts,
            latency_s=round(record.latency_s, 3),
            evidence_ids=record.evidence_ids,
            partial=record.partial,
        )


@dataclass
class RunMetrics:
    llm_calls: int = 0
    llm_errors: int = 0
    tool_calls: int = 0
    tool_attempts: int = 0
    tool_errors: int = 0
    usage: Usage = field(default_factory=Usage)
    served_models: list[str] = field(default_factory=list)
    fallback_calls: int = 0
    wall_s: float = 0.0
    per_agent: dict[str, dict[str, int]] = field(default_factory=dict)

    @classmethod
    def from_events(cls, events: list[dict[str, Any]], *, wall_s: float) -> "RunMetrics":
        metrics = cls(wall_s=round(wall_s, 3))
        served: set[str] = set()
        for event in events:
            agent = str(event.get("agent") or "-")
            bucket = metrics.per_agent.setdefault(
                agent, {"llm_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0}
            )
            if event["type"] == "llm_call":
                usage = Usage(**event["usage"])
                metrics.llm_calls += 1
                metrics.usage = metrics.usage + usage
                served.add(str(event["served_model"]))
                if event.get("fallback_used"):
                    metrics.fallback_calls += 1
                bucket["llm_calls"] += 1
                bucket["input_tokens"] += (
                    usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
                )
                bucket["output_tokens"] += usage.output_tokens
            elif event["type"] == "llm_error":
                metrics.llm_errors += 1
            elif event["type"] == "tool_call":
                metrics.tool_calls += 1
                metrics.tool_attempts += int(event.get("attempts", 0))
                if not event.get("ok"):
                    metrics.tool_errors += 1
                bucket["tool_calls"] += 1
        metrics.served_models = sorted(served)
        metrics.per_agent = {
            name: data for name, data in metrics.per_agent.items() if any(data.values())
        }
        return metrics

    def to_dict(self) -> dict[str, Any]:
        return {
            "llm_calls": self.llm_calls,
            "llm_errors": self.llm_errors,
            "tool_calls": self.tool_calls,
            "tool_attempts": self.tool_attempts,
            "tool_errors": self.tool_errors,
            **self.usage.to_dict(),
            "total_tokens": self.usage.total_tokens,
            "served_models": self.served_models,
            "fallback_calls": self.fallback_calls,
            "wall_s": self.wall_s,
            "per_agent": self.per_agent,
        }
