"""Submission gate: passes only once Task 7.4 is complete."""

import re
from pathlib import Path

WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'%.,$+-]*")


def test_technical_note_is_filled_in_and_at_most_500_words(repo_root: Path) -> None:
    text = (repo_root / "docs" / "technical-note.md").read_text(encoding="utf-8")
    assert "«" not in text and "»" not in text
    assert len(WORD.findall(text)) <= 500


def test_final_eval_results_are_committed(repo_root: Path) -> None:
    finals = sorted((repo_root / "evals" / "results").glob("final-*"))
    assert finals, "run Task 7.4 Step 4"
    for name in ("config.json", "results.jsonl", "results.md", "summary.json"):
        assert (finals[-1] / name).is_file(), name


def test_example_reports_exist(repo_root: Path) -> None:
    assert len(sorted((repo_root / "docs" / "examples").glob("*.md"))) >= 2


def test_transcripts_exist_and_contain_no_api_keys(repo_root: Path) -> None:
    files = [
        p
        for p in sorted((repo_root / "docs" / "transcripts").iterdir())
        if p.is_file() and p.name != "README.md"
    ]
    assert any(p.stat().st_size > 0 for p in files), "export the transcripts (Task 7.4 Step 7)"
    for path in files:
        assert "sk-ant-" not in path.read_text(encoding="utf-8", errors="ignore"), path.name
