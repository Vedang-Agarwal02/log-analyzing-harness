"""Replays recorded real-model runs offline. A cassette miss means a prompt, tool or transcript changed.

Cassettes are stored per model as tests/cassettes/<model id>/INC-01-<system>.json, so the model id comes
from the folder name and nothing here is tied to one provider. To record or refresh one:
`uv run investigator record-cassettes --scenario INC-01 --model <id> --out-dir tests/cassettes/<id> --yes`
"""

from pathlib import Path

import pytest

from investigator.harness.recording import RecordingLLM
from investigator.runtime import run_investigation
from investigator.settings import load_settings

CASSETTES = Path(__file__).resolve().parents[1] / "cassettes"
SYSTEMS = ("multi", "single")


def _recorded() -> list[tuple[str, str]]:
    folders = sorted(p for p in CASSETTES.iterdir() if p.is_dir()) if CASSETTES.is_dir() else []
    return [
        (folder.name, system)
        for folder in folders
        for system in SYSTEMS
        if (folder / f"INC-01-{system}.json").exists()
    ]


NOT_RECORDED = pytest.param(
    "",
    "",
    marks=pytest.mark.skip(
        reason="no cassettes recorded yet (run `uv run investigator record-cassettes --model <id> "
        "--out-dir tests/cassettes/<id> --yes`)"
    ),
)


@pytest.mark.parametrize("model,system", _recorded() or [NOT_RECORDED])
async def test_recorded_run_replays_exactly(repo_root: Path, model: str, system: str) -> None:
    settings = load_settings(
        repo_root / "config" / "default.yaml",
        {
            "llm": {"model": model},
            "simulation": {"latency_ms_min": 0, "latency_ms_max": 0},
            "tools": {"backoff_base_s": 0.0, "backoff_max_s": 0.0},
        },
    )
    replay = RecordingLLM(CASSETTES / model / f"INC-01-{system}.json", mode="replay")
    outcome = await run_investigation(
        scenario_id="INC-01", system=system, settings=settings, llm=replay
    )  # type: ignore[arg-type]
    assert replay.misses == 0 and replay.hits == outcome.metrics.llm_calls
    assert outcome.report.fallback is False
    cited = [e for h in outcome.report.hypotheses for e in h.supporting_evidence]
    assert all(outcome.evidence.has(e) for e in cited)
