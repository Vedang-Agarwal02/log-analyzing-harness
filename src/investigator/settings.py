"""Run configuration: config/default.yaml validated by Pydantic, with optional deep-merged overrides."""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from investigator.harness.anthropic_client import AnthropicSettings
from investigator.harness.budget import BudgetLimits
from investigator.harness.executor import ExecutorConfig
from investigator.harness.gemini_client import GeminiSettings
from investigator.harness.retry import RetryPolicy


class LLMConfig(BaseModel):
    model: str = "claude-opus-5-5"
    max_tokens: int = 32000
    max_retries: int = 3
    timeout_s: float = 600.0
    refusal_fallback: bool = True
    prompt_caching: bool = True
    gemini_tool_mode: Literal["AUTO", "VALIDATED"] = "AUTO"

    def anthropic_settings(self) -> AnthropicSettings:
        return AnthropicSettings(
            max_retries=self.max_retries,
            timeout_s=self.timeout_s,
            refusal_fallback=self.refusal_fallback,
            prompt_caching=self.prompt_caching,
        )

    def gemini_settings(self) -> GeminiSettings:
        return GeminiSettings(
            max_retries=self.max_retries, timeout_s=self.timeout_s, tool_mode=self.gemini_tool_mode
        )


class InvestigationConfig(BaseModel):
    max_rounds: int = 3
    max_parallel_tasks: int = 4
    max_tasks_total: int = 12
    max_initial_tasks: int = 6
    max_followup_tasks: int = 4
    max_task_attempts: int = 2
    task_timeout_s: float = 240.0
    stagnation_delta: float = 0.05
    critic_enabled: bool = True
    checks_enabled: bool = True
    default_lookback_hours: float = 3.0


class BudgetConfig(BaseModel):
    max_total_tokens: int = 1_500_000
    max_llm_calls: int = 120
    max_tool_calls: int = 250
    max_wall_seconds: float = 1800.0
    reserve_tokens: int = 150_000
    reserve_llm_calls: int = 8
    reserve_seconds: float = 120.0

    def limits(self) -> BudgetLimits:
        return BudgetLimits(**self.model_dump())


class ToolsConfig(BaseModel):
    default_timeout_s: float = 8.0
    max_attempts: int = 3
    backoff_base_s: float = 0.25
    backoff_max_s: float = 4.0
    breaker_threshold: int = 4
    breaker_cooldown_s: float = 30.0
    max_rate_limit_wait_s: float = 2.0

    def executor_config(self) -> ExecutorConfig:
        return ExecutorConfig(
            default_timeout_s=self.default_timeout_s,
            retry=RetryPolicy(
                max_attempts=self.max_attempts,
                base_delay_s=self.backoff_base_s,
                max_delay_s=self.backoff_max_s,
            ),
            breaker_threshold=self.breaker_threshold,
            breaker_cooldown_s=self.breaker_cooldown_s,
            max_rate_limit_wait_s=self.max_rate_limit_wait_s,
        )


class SimulationConfig(BaseModel):
    latency_ms_min: int = 30
    latency_ms_max: int = 200


class EvaluationConfig(BaseModel):
    trials: int = 3
    concurrency: int = 4
    run_timeout_s: float = 2700.0


class Settings(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    investigation: InvestigationConfig = Field(default_factory=InvestigationConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    agents_file: str = "config/agents.yaml"
    prompts_dir: str = "prompts"
    datasets_dir: str = "datasets/generated"
    runs_dir: str = "runs"

    def path(self, value: str) -> Path:
        candidate = Path(value)
        return candidate if candidate.is_absolute() else project_root() / candidate


def project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "config").is_dir():
            return candidate
    return current


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings(path: Path | None = None, overrides: dict[str, Any] | None = None) -> Settings:
    config_path = path or project_root() / "config" / "default.yaml"
    data: dict[str, Any] = {}
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return Settings.model_validate(_merge(data, overrides or {}))
