"""Aggregate results.jsonl into summary.json and results.md (multi-agent vs single-agent)."""

import json
import math
import statistics
from pathlib import Path
from typing import Any

QUALITY: list[tuple[str, str]] = [
    ("rc_correct", "Root-cause accuracy (top-1)"),
    ("top3_correct", "Top-3 accuracy"),
    ("trigger_correct", "Trigger accuracy (cases with a trigger)"),
    ("status_correct", "Status correct"),
    ("key_evidence_recall", "Key-evidence recall"),
    ("citation_validity", "Citation validity"),
    ("red_herring_avoided", "Red herring avoided"),
    ("data_gap_acknowledged", "Data gap acknowledged"),
    ("injection_resisted", "Prompt injection resisted"),
    ("brier", "Brier score (lower is better)"),
]
EFFICIENCY: list[tuple[str, str]] = [
    ("llm_calls", "LLM calls"),
    ("tool_calls", "Tool calls"),
    ("tool_attempts", "Tool attempts (incl. retries)"),
    ("tool_errors", "Failed tool calls"),
    ("input_tokens", "Uncached input tokens"),
    ("cache_read_tokens", "Cache-read tokens"),
    ("cache_write_tokens", "Cache-write tokens"),
    ("output_tokens", "Output tokens"),
    ("total_tokens", "Total tokens"),
    ("cost_usd", "Cost (USD)"),
    ("wall_s", "Wall-clock (s)"),
]


def load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _value(row: dict[str, Any], metric: str) -> float | None:
    if metric in row["score"]:
        value = row["score"][metric]
    elif metric == "cost_usd":
        value = row.get("cost_usd")
    else:
        value = row["metrics"].get(metric)
    return None if value is None else float(value)


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if row["status"] == "ok"]
    systems = sorted({row["system"] for row in scored})
    summary: dict[str, Any] = {
        "runs": len(rows),
        "scored_runs": len(scored),
        "excluded_runs": len(rows) - len(scored),
        "systems": {},
        "per_scenario": {},
        "per_tag": {},
    }
    for system in systems:
        mine = [row for row in scored if row["system"] == system]
        trials = sorted({row["trial"] for row in mine})
        block: dict[str, Any] = {"runs": len(mine)}
        for metric, _ in [*QUALITY, *EFFICIENCY]:
            values = [v for row in mine if (v := _value(row, metric)) is not None]
            per_trial: list[float] = []
            for trial in trials:
                trial_values = [
                    v
                    for row in mine
                    if row["trial"] == trial and (v := _value(row, metric)) is not None
                ]
                trial_mean = _mean(trial_values)
                if trial_mean is not None:
                    per_trial.append(trial_mean)
            sd = round(statistics.stdev(per_trial), 4) if len(per_trial) > 1 else None
            block[metric] = {"mean": _mean(values), "n": len(values), "trial_sd": sd}
        summary["systems"][system] = block
    for scenario in sorted({row["scenario"] for row in scored}):
        summary["per_scenario"][scenario] = {
            system: {
                "correct": sum(
                    row["score"]["rc_correct"]
                    for row in scored
                    if row["scenario"] == scenario and row["system"] == system
                ),
                "runs": sum(
                    1 for row in scored if row["scenario"] == scenario and row["system"] == system
                ),
            }
            for system in systems
        }
    for tag in sorted({tag for row in scored for tag in row["tags"]}):
        summary["per_tag"][tag] = {
            system: _mean(
                [
                    row["score"]["rc_correct"]
                    for row in scored
                    if row["system"] == system and tag in row["tags"]
                ]
            )
            for system in systems
        }
    return summary


def _cell(stats: dict[str, Any], digits: int) -> str:
    if stats["mean"] is None:
        return "n/a"
    sd = stats["trial_sd"]
    return f"{stats['mean']:.{digits}f}" + (f" ± {sd:.{digits}f}" if sd is not None else "")


def render_markdown(summary: dict[str, Any]) -> str:
    systems = list(summary["systems"])
    header = "| Metric | " + " | ".join(systems) + " |"
    divider = "|---|" + "---|" * len(systems)
    lines = [
        "# Evaluation results",
        "",
        f"Model: `{summary.get('model', '?')}` · runs: {summary['runs']} (scored {summary['scored_runs']}, "
        f"excluded {summary['excluded_runs']}) · errors.jsonl: {summary.get('errors', 0)}",
        "",
        "## Quality (mean over scored runs; ± is the standard deviation of per-trial means)",
        "",
        header,
        divider,
    ]
    for metric, label in QUALITY:
        lines.append(
            f"| {label} | "
            + " | ".join(_cell(summary["systems"][s][metric], 2) for s in systems)
            + " |"
        )
    lines += ["", "## Efficiency (mean per run)", "", header, divider]
    for metric, label in EFFICIENCY:
        digits = 3 if metric == "cost_usd" else 1
        lines.append(
            f"| {label} | "
            + " | ".join(_cell(summary["systems"][s][metric], digits) for s in systems)
            + " |"
        )
    lines += [
        "",
        "## Per scenario (root cause correct / runs)",
        "",
        "| Scenario | " + " | ".join(systems) + " |",
        divider,
    ]
    for scenario, by_system in summary["per_scenario"].items():
        cells = [f"{by_system[s]['correct']:.0f}/{by_system[s]['runs']}" for s in systems]
        lines.append(f"| {scenario} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## By tag (root-cause accuracy)",
        "",
        "| Tag | " + " | ".join(systems) + " |",
        divider,
    ]
    for tag, by_system in summary["per_tag"].items():
        cells = ["n/a" if by_system[s] is None else f"{by_system[s]:.2f}" for s in systems]
        lines.append(f"| {tag} | " + " | ".join(cells) + " |")
    runs_per_system = min((summary["systems"][s]["runs"] for s in systems), default=0)
    noise = 100 / math.sqrt(runs_per_system) if runs_per_system else float("nan")
    lines += [
        "",
        "## Notes",
        "",
        f"- With about {runs_per_system} runs per system, accuracy differences below roughly {noise:.0f} percentage points are within noise.",
        "- Runs answered by a different or fallback model are excluded from these tables (`excluded`).",
        "- Infrastructure and harness failures are listed in `errors.jsonl` and are never scored as wrong answers.",
    ]
    return "\n".join(lines) + "\n"


def write_summary(results_dir: Path) -> tuple[Path, dict[str, Any]]:
    rows = load_rows(results_dir / "results.jsonl")
    errors = load_rows(results_dir / "errors.jsonl")
    summary = summarize_rows(rows)
    summary["model"] = rows[0]["model"] if rows else "?"
    summary["errors"] = len(errors)
    (results_dir / "summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True), encoding="utf-8"
    )
    path = results_dir / "results.md"
    path.write_text(render_markdown(summary), encoding="utf-8")
    return path, summary
