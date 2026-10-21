"""Fill docs/technical-note.template.md with numbers from an evaluation results directory.

Usage: uv run python scripts/render_note.py evals/results/final-gemini-3.8-flash
Writes docs/technical-note.md and prints its word count (the assignment's limit is 500).
"""

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'%.,$+-]*")
PLACEHOLDER = re.compile(r"«([A-Z0-9_]+)»")


def word_count(text: str) -> int:
    return len(WORD.findall(text))


def _fmt(stats: dict[str, Any], spec: str) -> str:
    return "n/a" if stats["mean"] is None else format(stats["mean"], spec)


def _wins(per_scenario: dict[str, Any], better: str, worse: str) -> str:
    ids = [
        sid
        for sid, row in sorted(per_scenario.items())
        if row[better]["correct"] > row[worse]["correct"]
    ]
    return ", ".join(ids) if ids else "no scenario"


def note_values(summary: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    multi = summary["systems"]["multi"]
    single = summary["systems"]["single"]
    runs = min(multi["runs"], single["runs"])
    noise = round(100 / math.sqrt(runs)) if runs else 100
    diff = round(
        ((multi["rc_correct"]["mean"] or 0.0) - (single["rc_correct"]["mean"] or 0.0)) * 100
    )
    if abs(diff) < noise:
        verdict = f"The accuracy difference ({diff:+d} points) is inside the noise band, so accuracy alone does not separate the systems."
    elif diff > 0:
        verdict = f"Multi-agent accuracy was {diff} points higher, outside the noise band."
    else:
        verdict = f"The single agent was {-diff} points more accurate, outside the noise band."
    values = {
        "RUNS": str(runs),
        "TRIALS": str(config["trials"]),
        "MODEL": str(summary["model"]),
        "NOISE": str(noise),
        "VERDICT": verdict,
        "M_WINS": _wins(summary["per_scenario"], "multi", "single"),
        "S_WINS": _wins(summary["per_scenario"], "single", "multi"),
    }
    for prefix, block in (("M", multi), ("S", single)):
        values[f"{prefix}_RC"] = _fmt(block["rc_correct"], ".0%")
        values[f"{prefix}_TOP3"] = _fmt(block["top3_correct"], ".0%")
        values[f"{prefix}_CIT"] = _fmt(block["citation_validity"], ".0%")
        values[f"{prefix}_KEY"] = _fmt(block["key_evidence_recall"], ".0%")
        values[f"{prefix}_TOOLS"] = _fmt(block["tool_calls"], ".1f")
        values[f"{prefix}_TOK"] = _fmt(block["total_tokens"], ",.0f")
        values[f"{prefix}_COST"] = (
            "n/a" if block["cost_usd"]["mean"] is None else f"${block['cost_usd']['mean']:.2f}"
        )
        values[f"{prefix}_WALL"] = _fmt(block["wall_s"], ".0f") + " s"
    return values


def render(template: str, values: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda match: values[match.group(1)], template)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    results = Path(argv[1])
    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    config = json.loads((results / "config.json").read_text(encoding="utf-8"))
    template = (ROOT / "docs" / "technical-note.template.md").read_text(encoding="utf-8")
    text = render(template, note_values(summary, config))
    (ROOT / "docs" / "technical-note.md").write_text(text, encoding="utf-8")
    print(f"wrote docs/technical-note.md ({word_count(text)} words; the limit is 500)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
