"""Token pricing (USD per 1M tokens) for Claude and Gemini, and cost estimates.

Cost is always derived from recorded usage and the model that actually served each call.
Prices were taken from the providers' pricing pages on 2026-10-20 (standard tier). Google has announced
increases: Gemini 4 Argon to $4 / $20 / $0.20 from 2026-12-01, and Gemini 3.8 and 3.6 Flash to
$1.50 / $7.50 / $0.15 from 2027-01-01. Update this table when prices change.
"""

from dataclasses import dataclass
from typing import Any

from investigator.harness.llm import Usage


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_write: float
    cache_read: float


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_write=5.00, cache_read=0.20),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_write=2.50, cache_read=0.20),
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_write=0.125, cache_read=0.01),
    # Gemini caches repeated prompt prefixes implicitly, so there is no cache-write charge.
    "gemini-4-argon": Price(input=2.00, output=10.00, cache_write=0.0, cache_read=0.10),
    "gemini-3.8-flash": Price(input=0.75, output=3.75, cache_write=0.0, cache_read=0.075),
    "gemini-3.6-flash": Price(input=0.75, output=3.75, cache_write=0.0, cache_read=0.075),
    "gemini-3.5-flash-lite": Price(input=0.30, output=2.50, cache_write=0.0, cache_read=0.03),
    "gemini-3.1-pro-preview": Price(input=2.00, output=12.00, cache_write=0.0, cache_read=0.20),
}
# Models whose rate card changes above a prompt length: model -> (prompt tokens, price above that length).
LONG_PROMPT: dict[str, tuple[int, Price]] = {
    "claude-haiku-5-5": (
        100_000,
        Price(input=0.50, output=2.50, cache_write=0.625, cache_read=0.05),
    ),
    "gemini-3.1-pro-preview": (
        200_000,
        Price(input=4.00, output=18.00, cache_write=0.0, cache_read=0.40),
    ),
}

# Rough per-run volume used only for the pre-run estimate; replace it with measured smoke-run numbers.
TYPICAL_RUN = Usage(
    input_tokens=237_000, output_tokens=39_000, cache_read_tokens=144_000, cache_write_tokens=0
)


def price_for(model: str, prompt_tokens: int) -> Price | None:
    """Match the model id exactly, or as a dated or variant id of a priced model (e.g. `<id>-20261001`)."""
    matches = [name for name in PRICES if model == name or model.startswith(f"{name}-")]
    if not matches:
        return None
    base = max(matches, key=len)
    long_prompt = LONG_PROMPT.get(base)
    if long_prompt is not None and prompt_tokens > long_prompt[0]:
        return long_prompt[1]
    return PRICES[base]


def call_cost(model: str, usage: dict[str, int]) -> float | None:
    uncached = usage.get("input_tokens", 0)
    cache_read = usage.get("cache_read_tokens", 0)
    cache_write = usage.get("cache_write_tokens", 0)
    price = price_for(model, uncached + cache_read + cache_write)
    if price is None:
        return None
    total = (
        uncached * price.input
        + usage.get("output_tokens", 0) * price.output
        + cache_write * price.cache_write
        + cache_read * price.cache_read
    )
    return total / 1_000_000


def run_cost(events: list[dict[str, Any]]) -> float | None:
    total = 0.0
    priced = False
    for event in events:
        if event.get("type") != "llm_call":
            continue
        cost = call_cost(str(event.get("served_model") or event.get("model")), dict(event["usage"]))
        if cost is not None:
            total += cost
            priced = True
    return round(total, 6) if priced else None


def estimate_cost_text(model: str, runs: int) -> str:
    one = call_cost(model, TYPICAL_RUN.to_dict())
    if one is None:
        return f"No price on file for {model}; cost will not be estimated."
    return f"Rough estimate: ~${one:.2f} per run, ~${one * runs:.2f} in total (replace with a measured smoke run)."
