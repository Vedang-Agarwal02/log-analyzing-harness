import json
from pathlib import Path
from typing import Any

from investigator.evaluation.summary import render_markdown, summarize_rows, write_summary


def _row(
    scenario: str,
    system: str,
    trial: int,
    correct: float,
    status: str = "ok",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "key": f"{scenario}|{system}|{trial}",
        "scenario": scenario,
        "system": system,
        "trial": trial,
        "tags": tags or ["misleading_evidence"],
        "status": status,
        "model": "claude-opus-5-5",
        "score": {
            "rc_correct": correct,
            "top3_correct": correct,
            "trigger_correct": None,
            "status_correct": 1.0,
            "citation_validity": 1.0,
            "key_evidence_recall": 0.5,
            "red_herring_avoided": None,
            "data_gap_acknowledged": None,
            "injection_resisted": None,
            "brier": 0.1,
            "top_confidence": 0.8,
        },
        "metrics": {
            "llm_calls": 10,
            "tool_calls": 8,
            "tool_attempts": 9,
            "tool_errors": 1,
            "input_tokens": 100,
            "cache_read_tokens": 1000,
            "cache_write_tokens": 50,
            "output_tokens": 300,
            "total_tokens": 1450,
            "wall_s": 60.0,
        },
        "cost_usd": 1.25,
    }


ROWS = [
    _row("INC-01", "multi", 1, 1.0),
    _row("INC-01", "multi", 2, 0.0),
    _row("INC-01", "single", 1, 0.0),
    _row("INC-01", "single", 2, 0.0),
    _row("INC-02", "multi", 1, 1.0, status="model_mismatch"),
]


def test_summarize_rows_means_and_exclusions() -> None:
    summary = summarize_rows(ROWS)
    assert summary["runs"] == 5 and summary["scored_runs"] == 4 and summary["excluded_runs"] == 1
    multi = summary["systems"]["multi"]
    assert multi["rc_correct"]["mean"] == 0.5 and multi["rc_correct"]["n"] == 2
    assert multi["rc_correct"]["trial_sd"] == 0.7071
    assert multi["trigger_correct"]["mean"] is None
    assert multi["cost_usd"]["mean"] == 1.25 and multi["wall_s"]["mean"] == 60.0
    assert summary["per_scenario"]["INC-01"]["multi"] == {"correct": 1.0, "runs": 2}
    assert summary["per_tag"]["misleading_evidence"]["single"] == 0.0


def test_render_markdown_has_tables_and_noise_note() -> None:
    text = render_markdown(summarize_rows(ROWS) | {"model": "claude-opus-5-5", "errors": 2})
    assert "| Root-cause accuracy (top-1) | 0.50 ± 0.71 | 0.00 ± 0.00 |" in text
    assert "## Per scenario" in text and "| INC-01 | 1/2 | 0/2 |" in text
    assert "within noise" in text and "errors.jsonl: 2" in text


def test_write_summary(tmp_path: Path) -> None:
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in ROWS) + "\n")
    path, summary = write_summary(tmp_path)
    assert path == tmp_path / "results.md" and path.exists()
    assert json.loads((tmp_path / "summary.json").read_text())["scored_runs"] == 4
    assert summary["model"] == "claude-opus-5-5" and summary["errors"] == 0
