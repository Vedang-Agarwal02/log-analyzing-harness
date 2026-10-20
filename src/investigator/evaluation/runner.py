"""Evaluation runner: scenarios x systems x trials. Each run is scored and appended to results.jsonl.

- Resumable: rows already in results.jsonl (by key) are skipped.
- Infrastructure or harness failures go to errors.jsonl and are never scored as wrong answers.
- Runs served by a different model (or a fallback model) are flagged status=model_mismatch.
"""

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from investigator.evaluation.ground_truth import load_ground_truth
from investigator.evaluation.pricing import run_cost
from investigator.evaluation.scoring import score_run
from investigator.harness.llm import LLMClient
from investigator.runtime import run_investigation, scenario_dir
from investigator.settings import Settings


def job_key(scenario: str, system: str, trial: int) -> str:
    return f"{scenario}|{system}|{trial}"


def completed_keys(results_path: Path) -> set[str]:
    if not results_path.exists():
        return set()
    return {
        json.loads(line)["key"]
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


async def run_eval(
    *,
    scenario_ids: list[str],
    systems: list[str],
    trials: int,
    settings: Settings,
    out_dir: Path,
    concurrency: int,
    llm_factory: Callable[[], LLMClient],
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    errors_path = out_dir / "errors.jsonl"
    done = completed_keys(results_path)
    jobs = [
        (scenario, system, trial)
        for scenario in scenario_ids
        for system in systems
        for trial in range(1, trials + 1)
        if job_key(scenario, system, trial) not in done
    ]
    config = {
        "model": settings.llm.model,
        "scenarios": scenario_ids,
        "systems": systems,
        "trials": trials,
        "settings": settings.model_dump(mode="json"),
    }
    (out_dir / "config.json").write_text(
        json.dumps(config, indent=1, sort_keys=True, default=str), encoding="utf-8"
    )
    semaphore = asyncio.Semaphore(max(1, concurrency))
    rows: list[dict[str, Any]] = []

    async def one(scenario: str, system: str, trial: int) -> None:
        key = job_key(scenario, system, trial)
        async with semaphore:
            run_dir = out_dir / "runs" / scenario / system / f"trial-{trial}"
            started = time.monotonic()
            try:
                outcome = await asyncio.wait_for(
                    run_investigation(
                        scenario_id=scenario,
                        system=system,  # type: ignore[arg-type]
                        settings=settings,
                        llm=llm_factory(),
                        run_dir=run_dir,
                        seed=trial,
                        run_id=f"{scenario}-{system}-t{trial}",
                    ),
                    timeout=settings.evaluation.run_timeout_s,
                )
            except Exception as exc:
                error_class = "timeout" if isinstance(exc, TimeoutError) else "harness_error"
                append_jsonl(
                    errors_path,
                    {
                        "key": key,
                        "scenario": scenario,
                        "system": system,
                        "trial": trial,
                        "error_class": error_class,
                        "error": f"{type(exc).__name__}: {exc}",
                        "wall_s": round(time.monotonic() - started, 3),
                    },
                )
                if progress is not None:
                    progress(f"ERROR {key}: {error_class}: {exc}")
                return
            truth = load_ground_truth(scenario_dir(settings, scenario) / "ground_truth.json")
            score = score_run(outcome.report, outcome.evidence, truth)
            served = outcome.metrics.served_models
            requested = settings.llm.model
            same_model = all(m == requested or m.startswith(f"{requested}-") for m in served)
            status = (
                "ok" if same_model and outcome.metrics.fallback_calls == 0 else "model_mismatch"
            )
            top = (
                outcome.report.hypotheses[0].model_dump(mode="json")
                if outcome.report.hypotheses
                else None
            )
            row = {
                "key": key,
                "scenario": scenario,
                "system": system,
                "trial": trial,
                "tags": truth.tags,
                "expected_status": truth.expected_status,
                "status": status,
                "report_status": outcome.report.status,
                "termination_reason": outcome.report.termination_reason,
                "fallback_report": outcome.report.fallback,
                "top": top,
                "score": score.to_dict(),
                "metrics": outcome.metrics.to_dict(),
                "cost_usd": run_cost(outcome.events),
                "model": settings.llm.model,
                "served_models": served,
            }
            append_jsonl(results_path, row)
            rows.append(row)
            if progress is not None:
                progress(
                    f"done {key}: correct={score.rc_correct} status={outcome.report.status} cost={row['cost_usd']}"
                )

    await asyncio.gather(*(one(*job) for job in jobs))
    return rows
