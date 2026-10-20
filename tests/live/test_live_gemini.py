"""Live check of the Gemini path (well under $1). Run with: uv run pytest tests/live/test_live_gemini.py -m live -q

The model defaults to gemini-3.8-flash; set GEMINI_SMOKE_MODEL to try another one, e.g. gemini-4-argon.
"""

import os

import pytest

from investigator.runtime import build_llm, run_investigation
from investigator.settings import load_settings

GEMINI_SMOKE_MODEL = os.environ.get("GEMINI_SMOKE_MODEL", "gemini-3.8-flash")
HAS_KEY = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


@pytest.mark.live
@pytest.mark.skipif(not HAS_KEY, reason="needs GEMINI_API_KEY (or GOOGLE_API_KEY)")
@pytest.mark.parametrize("system", ["multi", "single"])
async def test_live_gemini_run_completes(system: str) -> None:
    settings = load_settings(None, {"llm": {"model": GEMINI_SMOKE_MODEL}})
    outcome = await run_investigation(
        scenario_id="INC-01", system=system, settings=settings, llm=build_llm(settings)
    )  # type: ignore[arg-type]
    assert outcome.metrics.llm_errors == 0, [e for e in outcome.events if e["type"] == "llm_error"]
    assert outcome.report.fallback is False
    assert all(m.startswith(GEMINI_SMOKE_MODEL) for m in outcome.metrics.served_models)
