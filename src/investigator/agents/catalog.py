"""Loads config/agents.yaml into AgentSpecs. A new specialist needs only a YAML entry and a prompt file."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from investigator.agents.outputs import OUTPUT_SPECS, OutputKind
from investigator.harness.agent import AgentSpec

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class AgentConfig(BaseModel):
    prompt: str
    output: OutputKind
    description: str = ""
    tools: list[str] = Field(default_factory=list)
    model: str | None = None
    effort: Effort = "medium"
    max_turns: int = 12
    max_tool_calls: int = 16
    max_tokens: int | None = None
    use_reserve: bool = False


class CatalogConfig(BaseModel):
    planner: AgentConfig
    specialists: dict[str, AgentConfig]
    reasoning: AgentConfig
    critic: AgentConfig
    reporter: AgentConfig
    single_agent: AgentConfig


@dataclass(frozen=True)
class AgentCatalog:
    planner: AgentSpec
    specialists: dict[str, AgentSpec]
    reasoning: AgentSpec
    critic: AgentSpec
    reporter: AgentSpec
    single_agent: AgentSpec
    descriptions: dict[str, str]

    def all_specs(self) -> list[AgentSpec]:
        return [
            self.planner,
            *self.specialists.values(),
            self.reasoning,
            self.critic,
            self.reporter,
            self.single_agent,
        ]


def load_catalog(
    agents_file: Path, prompts_dir: Path, *, default_model: str, default_max_tokens: int = 32000
) -> AgentCatalog:
    config = CatalogConfig.model_validate(yaml.safe_load(agents_file.read_text(encoding="utf-8")))
    shared = (prompts_dir / "shared_rules.md").read_text(encoding="utf-8").strip()

    def build(name: str, agent: AgentConfig) -> AgentSpec:
        prompt = (prompts_dir / agent.prompt).read_text(encoding="utf-8").strip()
        return AgentSpec(
            name=name,
            system_prompt=f"{prompt}\n\n{shared}",
            model=agent.model or default_model,
            effort=agent.effort,
            tools=tuple(sorted(agent.tools)),
            output=OUTPUT_SPECS[agent.output],
            max_turns=agent.max_turns,
            max_tool_calls=agent.max_tool_calls,
            max_tokens=agent.max_tokens or default_max_tokens,
            use_reserve=agent.use_reserve,
        )

    specialists = {name: build(name, agent) for name, agent in sorted(config.specialists.items())}
    for name, agent in config.specialists.items():
        if agent.output != "finding":
            raise ValueError(f"specialist {name} must use output 'finding'")
    return AgentCatalog(
        planner=build("planner", config.planner),
        specialists=specialists,
        reasoning=build("reasoning", config.reasoning),
        critic=build("critic", config.critic),
        reporter=build("reporter", config.reporter),
        single_agent=build("single_agent", config.single_agent),
        descriptions={
            name: agent.description for name, agent in sorted(config.specialists.items())
        },
    )
