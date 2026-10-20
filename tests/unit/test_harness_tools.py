import json
from typing import Literal

import pytest
from pydantic import BaseModel, Field, ValidationError

from investigator.domain.common import UtcDatetime
from investigator.domain.evidence import EvidenceStore
from investigator.harness.schema import format_validation_error, model_input_schema
from investigator.harness.tools import Tool, ToolContext, ToolOutput, ToolRegistry, tool


class Inner(BaseModel):
    title: str
    kind: Literal["a", "b"]


class Outer(BaseModel):
    """Outer model."""

    inner: Inner
    items: list[Inner] = Field(default_factory=list)
    when: UtcDatetime | None = None


class EchoInput(BaseModel):
    text: str
    times: int = Field(default=1, ge=1, le=3)


@tool(name="echo", description="Echo text back.", input_model=EchoInput)
async def echo(args: EchoInput, ctx: ToolContext) -> ToolOutput:
    return ToolOutput(data={"text": args.text * args.times, "caller": ctx.caller})


@tool(name="shout", description="Echo text in upper case.", input_model=EchoInput)
async def shout(args: EchoInput, ctx: ToolContext) -> ToolOutput:
    return ToolOutput(data={"text": args.text.upper()})


def test_schema_is_inlined_and_keeps_title_property() -> None:
    schema = model_input_schema(Outer)
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema
    assert "title" not in schema
    inner = schema["properties"]["inner"]
    assert inner["properties"]["title"] == {"type": "string"}
    assert inner["properties"]["kind"]["enum"] == ["a", "b"]
    assert schema["properties"]["items"]["items"]["properties"]["kind"]["enum"] == ["a", "b"]
    assert schema["description"] == "Outer model."


def test_format_validation_error_lists_locations() -> None:
    with pytest.raises(ValidationError) as caught:
        Outer.model_validate({"inner": {"title": 1, "kind": "z"}})
    text = format_validation_error(caught.value)
    assert "inner.title" in text and "inner.kind" in text


async def test_function_tool_parses_and_runs() -> None:
    parsed = echo.parse_arguments({"text": "ab", "times": 2})
    output = await echo.run(parsed, ToolContext(caller="task-1", evidence=EvidenceStore()))
    assert output.data == {"text": "abab", "caller": "task-1"}
    with pytest.raises(ValidationError):
        echo.parse_arguments({"text": "x", "times": 9})
    assert isinstance(echo, Tool)


def test_registry_subset_specs_duplicates_and_toolsets() -> None:
    registry = ToolRegistry([echo])
    assert "echo" in registry and registry.names() == ["echo"]
    spec = registry.specs()[0]
    assert spec.name == "echo" and spec.input_schema["required"] == ["text"]
    assert registry.subset(["echo"]).names() == ["echo"]
    with pytest.raises(KeyError):
        registry.subset(["nope"])
    with pytest.raises(ValueError):
        registry.register(echo)

    class Shouty:
        def tools(self) -> list[Tool]:
            return [shout]

    registry.register_toolset(Shouty())
    assert registry.names() == ["echo", "shout"]
    assert registry.get("missing") is None
