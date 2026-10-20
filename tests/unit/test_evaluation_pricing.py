import pytest

from investigator.evaluation.pricing import call_cost, estimate_cost_text, run_cost


def test_call_cost_opus() -> None:
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 100_000,
        "cache_read_tokens": 1_000_000,
        "cache_write_tokens": 100_000,
    }
    assert call_cost("claude-opus-5-5", usage) == pytest.approx(4.0 + 2.0 + 0.2 + 0.5)
    assert call_cost("claude-opus-5-5-20261001", usage) == call_cost("claude-opus-5-5", usage)


def test_haiku_long_prompt_rate_and_unknown_model() -> None:
    short = {
        "input_tokens": 50_000,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    long = {
        "input_tokens": 200_000,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    assert call_cost("claude-haiku-5-5", short) == pytest.approx(0.005)
    assert call_cost("claude-haiku-5-5", long) == pytest.approx(0.1)
    assert call_cost("scripted", short) is None


def test_gemini_prices_and_long_prompt_tier() -> None:
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read_tokens": 1_000_000,
        "cache_write_tokens": 0,
    }
    assert call_cost("gemini-4-argon", usage) == pytest.approx(2.0 + 10.0 + 0.1)
    short = {
        "input_tokens": 100_000,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    long = {
        "input_tokens": 300_000,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    assert call_cost("gemini-3.1-pro-preview", short) == pytest.approx(0.2)
    assert call_cost("gemini-3.1-pro-preview", long) == pytest.approx(1.2)
    assert call_cost("gemini-3.1-pro-preview-customtools", short) == pytest.approx(0.2)
    assert call_cost("gemini-2.5-pro", short) is None


def test_run_cost_sums_llm_calls_by_served_model() -> None:
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    events = [
        {
            "type": "llm_call",
            "model": "claude-opus-5-5",
            "served_model": "claude-opus-5-5",
            "usage": usage,
        },
        {
            "type": "llm_call",
            "model": "claude-opus-5-5",
            "served_model": "claude-sonnet-5-5",
            "usage": usage,
        },
        {"type": "tool_call"},
    ]
    assert run_cost(events) == pytest.approx(6.0)
    assert run_cost([{"type": "llm_call", "served_model": "scripted", "usage": usage}]) is None


def test_estimate_text() -> None:
    assert "per run" in estimate_cost_text("claude-opus-5-5", 72)
    assert "No price on file" in estimate_cost_text("scripted", 4)
