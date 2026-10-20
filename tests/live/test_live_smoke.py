"""Live check against the real API (costs about $2). Run with: uv run pytest -m live -q"""

import pytest

from investigator.runtime import build_llm, run_investigation
from investigator.settings import load_settings


@pytest.mark.live
@pytest.mark.parametrize("system", ["multi", "single"])
async def test_live_run_completes_without_api_errors(system: str) -> None:
    settings = load_settings()
    outcome = await run_investigation(
        scenario_id="INC-01", system=system, settings=settings, llm=build_llm(settings)
    )  # type: ignore[arg-type]
    assert outcome.metrics.llm_errors == 0, [e for e in outcome.events if e["type"] == "llm_error"]
    assert outcome.report.fallback is False
    model = settings.llm.model
    assert all(m == model or m.startswith(f"{model}-") for m in outcome.metrics.served_models)
