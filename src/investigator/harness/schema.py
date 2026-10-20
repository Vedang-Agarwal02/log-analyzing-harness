"""Turn Pydantic models into self-contained JSON Schemas for tool definitions."""

from typing import Any

from pydantic import BaseModel, ValidationError


def model_input_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema for `model` with every `$ref` inlined and `title` annotations removed."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})
    result: dict[str, Any] = _inline(schema, definitions)
    return result


def _inline(node: Any, definitions: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_inline(item, definitions) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = str(node["$ref"]).rsplit("/", 1)[-1]
        merged = {
            **definitions[name],
            **{key: value for key, value in node.items() if key != "$ref"},
        }
        return _inline(merged, definitions)
    result: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title":
            continue
        if key == "properties" and isinstance(value, dict):
            result[key] = {name: _inline(sub, definitions) for name, sub in value.items()}
        else:
            result[key] = _inline(value, definitions)
    return result


def format_validation_error(error: ValidationError, *, limit: int = 12) -> str:
    """Compact, model-friendly description of a Pydantic validation error."""
    problems = error.errors()
    lines = []
    for item in problems[:limit]:
        location = ".".join(str(part) for part in item["loc"]) or "(root)"
        lines.append(f"{location}: {item['msg']}")
    if len(problems) > limit:
        lines.append(f"... and {len(problems) - limit} more problems")
    return "; ".join(lines)
