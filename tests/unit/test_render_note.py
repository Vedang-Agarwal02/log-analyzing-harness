import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from investigator.evaluation.summary import summarize_rows


def _load(repo_root: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "render_note", repo_root / "scripts" / "render_note.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(scenario: str, system: str, trial: int, correct: float) -> dict[str, Any]:
    score = {
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
    }
    metrics = {
        "llm_calls": 10,
        "tool_calls": 8,
        "tool_attempts": 9,
        "tool_errors": 0,
        "input_tokens": 100,
        "cache_read_tokens": 1000,
        "cache_write_tokens": 50,
        "output_tokens": 300,
        "total_tokens": 1450,
        "wall_s": 60.0,
    }
    return {
        "key": f"{scenario}|{system}|{trial}",
        "scenario": scenario,
        "system": system,
        "trial": trial,
        "tags": ["t"],
        "status": "ok",
        "model": "claude-opus-5-5",
        "score": score,
        "metrics": metrics,
        "cost_usd": 1.25,
    }


def _summary() -> dict[str, Any]:
    rows = [_row(s, "multi", t, 1.0) for s in ("INC-01", "INC-02") for t in (1, 2)]
    rows += [_row(s, "single", t, 0.0) for s in ("INC-01", "INC-02") for t in (1, 2)]
    return summarize_rows(rows) | {"model": "claude-opus-5-5", "errors": 0}


def test_note_values_compare_the_systems(repo_root: Path) -> None:
    values = _load(repo_root).note_values(_summary(), {"trials": 2})
    assert values["RUNS"] == "4" and values["TRIALS"] == "2" and values["NOISE"] == "50"
    assert values["M_RC"] == "100%" and values["S_RC"] == "0%" and values["M_COST"] == "$1.25"
    assert values["VERDICT"].startswith("Multi-agent accuracy was 100 points higher")
    assert values["M_WINS"] == "INC-01, INC-02" and values["S_WINS"] == "no scenario"


def test_template_renders_completely_within_500_words(repo_root: Path) -> None:
    module = _load(repo_root)
    template = (repo_root / "docs" / "technical-note.template.md").read_text(encoding="utf-8")
    text = module.render(template, module.note_values(_summary(), {"trials": 2}))
    assert "«" not in text and "»" not in text
    assert module.word_count(text) <= 500
