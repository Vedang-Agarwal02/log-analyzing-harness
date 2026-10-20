"""Deterministic JSON helpers. Everything that reaches a prompt or a tool result uses these."""

import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Compact JSON with sorted keys (stable bytes for caching, hashing and replay)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def pretty_json(value: Any) -> str:
    """Indented JSON with sorted keys, for human-readable prompt sections."""
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, default=str)
