"""Command-line interface: generate data, investigate, evaluate, record cassettes, inspect traces."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.tree import Tree

from investigator.datagen.generate import generate_all
from investigator.runtime import build_llm, list_scenarios, new_run_id, run_investigation
from investigator.settings import Settings, load_settings, project_root

app = typer.Typer(
    add_completion=False, no_args_is_help=True, help="Multi-agent production incident investigator."
)
console = Console()

ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", help="Path to a YAML config (default: config/default.yaml)"),
]
ModelOption = Annotated[
    str | None,
    typer.Option("--model", help="Model id override, e.g. claude-haiku-5-5 or gemini-4-argon"),
]


def _settings(config: Path | None, model: str | None = None, no_critic: bool = False) -> Settings:
    overrides: dict[str, Any] = {}
    if model:
        overrides["llm"] = {"model": model}
    if no_critic:
        overrides["investigation"] = {"critic_enabled": False}
    return load_settings(config, overrides)


@app.command("generate-data")
def generate_data() -> None:
    """Generate datasets/generated/ from datasets/scenarios/*.yaml (deterministic)."""
    root = project_root()
    scenarios = root / "datasets" / "scenarios"
    for path in generate_all(
        scenarios, root / "datasets" / "platform.yaml", root / "datasets" / "generated"
    ):
        console.print(f"generated {path.relative_to(root)}")


@app.command("list-scenarios")
def list_scenarios_command(config: ConfigOption = None) -> None:
    """List generated scenarios and their titles."""
    settings = _settings(config)
    for scenario_id in list_scenarios(settings):
        incident = json.loads(
            (settings.path(settings.datasets_dir) / scenario_id / "incident.json").read_text(
                encoding="utf-8"
            )
        )
        console.print(f"{scenario_id}  {escape(incident['title'])}")


@app.command()
def investigate(
    scenario: Annotated[str, typer.Option(help="Scenario id, e.g. INC-01")] = "INC-01",
    system: Annotated[
        str, typer.Option(help="'multi' (six agents) or 'single' (baseline)")
    ] = "multi",
    model: ModelOption = None,
    config: ConfigOption = None,
    record: Annotated[
        Path | None, typer.Option(help="Record LLM responses to this cassette file")
    ] = None,
    replay: Annotated[
        Path | None, typer.Option(help="Replay LLM responses from this cassette (offline)")
    ] = None,
    no_critic: Annotated[
        bool, typer.Option("--no-critic", help="Ablation: skip the critic loop")
    ] = False,
) -> None:
    """Run one investigation; writes report.md, report.json and trace.jsonl under runs/."""
    if system not in ("multi", "single"):
        raise typer.BadParameter("system must be 'multi' or 'single'")
    settings = _settings(config, model, no_critic)
    run_id = new_run_id(scenario, system)
    run_dir = settings.path(settings.runs_dir) / run_id
    llm = build_llm(settings, record=record, replay=replay)
    outcome = asyncio.run(
        run_investigation(
            scenario_id=scenario,
            system=system,  # type: ignore[arg-type]
            settings=settings,
            llm=llm,
            run_dir=run_dir,
            run_id=run_id,
        )
    )
    report = outcome.report
    usage = outcome.metrics.usage
    table = Table(title=f"{scenario} ({system})")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Status", report.status)
    table.add_row("Termination", report.termination_reason)
    if report.hypotheses:
        top = report.hypotheses[0]
        table.add_row(
            "Top hypothesis",
            escape(
                f"{top.title} [{top.root_cause_service} / {top.category}] ({top.confidence:.2f})"
            ),
        )
    table.add_row(
        "LLM calls / tool calls", f"{outcome.metrics.llm_calls} / {outcome.metrics.tool_calls}"
    )
    table.add_row(
        "Input tokens (incl. cache) / output",
        f"{usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens} / {usage.output_tokens}",
    )
    table.add_row("Wall-clock", f"{outcome.metrics.wall_s:.1f}s")
    table.add_row("Report", str(run_dir / "report.md"))
    console.print(table)


@app.command("show-trace")
def show_trace(
    run_dir: Annotated[Path, typer.Argument(help="A run directory containing trace.jsonl")],
) -> None:
    """Print a run's trace as a tree of phases, tasks, LLM calls and tool calls."""
    lines = (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    tree = Tree(escape(f"run {events[0]['run_id'] if events else '?'}"))
    branch = tree
    for event in events:
        kind = event["type"]
        if kind == "phase":
            branch = tree.add(escape(f"{event['name']} (round {event.get('round')})"))
        elif kind == "llm_call":
            usage = event["usage"]
            prompt = (
                usage["input_tokens"] + usage["cache_read_tokens"] + usage["cache_write_tokens"]
            )
            branch.add(
                escape(
                    f"llm {event['agent']} [{event.get('task_id') or '-'}] stop={event['stop_reason']} in={prompt} out={usage['output_tokens']} {event['latency_s']}s"
                )
            )
        elif kind == "tool_call":
            status = "ok" if event["ok"] else f"ERROR {event['error_type']}"
            branch.add(
                escape(
                    f"tool {event['tool']} by {event['agent']} [{status}] attempts={event['attempts']}"
                )
            )
        elif kind in (
            "task_added",
            "task_end",
            "hypotheses",
            "checks",
            "critique",
            "fallback",
            "termination",
            "error",
        ):
            details = {
                key: value for key, value in event.items() if key not in ("ts", "type", "run_id")
            }
            branch.add(escape(f"{kind}: {json.dumps(details, default=str)[:240]}"))
    console.print(tree)


@app.command("eval")
def eval_command(
    scenarios: Annotated[str, typer.Option(help="'all' or comma-separated ids")] = "all",
    systems: Annotated[str, typer.Option(help="Comma-separated: multi,single")] = "multi,single",
    trials: Annotated[int | None, typer.Option(help="Trials per scenario and system")] = None,
    concurrency: Annotated[int | None, typer.Option(help="Runs in parallel")] = None,
    model: ModelOption = None,
    out: Annotated[
        Path | None, typer.Option(help="Results directory (resumes if it exists)")
    ] = None,
    config: ConfigOption = None,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the cost confirmation")] = False,
    no_critic: Annotated[
        bool, typer.Option("--no-critic", help="Ablation: multi-agent without the critic loop")
    ] = False,
    min_accuracy: Annotated[
        float | None, typer.Option(help="Exit 2 if multi-agent accuracy is below this")
    ] = None,
) -> None:
    """Run the evaluation (real API calls) and write results.jsonl, results.md and summary.json."""
    from investigator.evaluation.pricing import estimate_cost_text
    from investigator.evaluation.runner import run_eval
    from investigator.evaluation.summary import write_summary

    settings = _settings(config, model, no_critic)
    scenario_ids = (
        list_scenarios(settings)
        if scenarios == "all"
        else [s.strip() for s in scenarios.split(",") if s.strip()]
    )
    system_list = [s.strip() for s in systems.split(",") if s.strip()]
    trial_count = trials or settings.evaluation.trials
    parallel = concurrency or settings.evaluation.concurrency
    out_dir = (
        out
        or settings.path("evals/results")
        / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{settings.llm.model}"
    )
    runs = len(scenario_ids) * len(system_list) * trial_count
    console.print(
        f"{runs} runs on {settings.llm.model}: {len(scenario_ids)} scenarios x {len(system_list)} systems x {trial_count} trials"
    )
    console.print(estimate_cost_text(settings.llm.model, runs))
    if not yes and not typer.confirm("This calls the real API and costs money. Continue?"):
        raise typer.Exit(1)
    llm = build_llm(settings)
    asyncio.run(
        run_eval(
            scenario_ids=scenario_ids,
            systems=system_list,
            trials=trial_count,
            settings=settings,
            out_dir=out_dir,
            concurrency=parallel,
            llm_factory=lambda: llm,
            progress=lambda message: console.print(escape(message)),
        )
    )
    summary_path, summary = write_summary(out_dir)
    console.print(f"results: {summary_path}")
    if min_accuracy is not None:
        accuracy = summary["systems"].get("multi", {}).get("rc_correct", {}).get("mean")
        if accuracy is None or accuracy < min_accuracy:
            console.print(f"quality gate failed: multi-agent accuracy {accuracy} < {min_accuracy}")
            raise typer.Exit(2)


@app.command()
def summarize(
    results_dir: Annotated[Path, typer.Argument(help="An eval results directory")],
) -> None:
    """Rebuild results.md and summary.json from results.jsonl."""
    from investigator.evaluation.summary import write_summary

    if not (results_dir / "results.jsonl").exists():
        console.print(f"no results.jsonl in {results_dir}")
        raise typer.Exit(1)
    path, _ = write_summary(results_dir)
    console.print(f"wrote {path}")


@app.command("record-cassettes")
def record_cassettes(
    scenario: Annotated[str, typer.Option(help="Scenario to record")] = "INC-01",
    systems: Annotated[str, typer.Option(help="Comma-separated: multi,single")] = "multi,single",
    out_dir: Annotated[Path, typer.Option(help="Where cassettes are written")] = Path(
        "tests/cassettes"
    ),
    model: ModelOption = None,
    config: ConfigOption = None,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation")] = False,
) -> None:
    """Record real LLM responses for replay tests (calls the real API)."""
    settings = _settings(config, model)
    if not yes and not typer.confirm(
        f"Record {systems} on {scenario} with {settings.llm.model}? This costs money."
    ):
        raise typer.Exit(1)
    for system in [s.strip() for s in systems.split(",") if s.strip()]:
        cassette = out_dir / f"{scenario}-{system}.json"
        if cassette.exists():
            cassette.unlink()
        llm = build_llm(settings, record=cassette)
        outcome = asyncio.run(
            run_investigation(
                scenario_id=scenario,
                system=system,  # type: ignore[arg-type]
                settings=settings,
                llm=llm,
                run_dir=None,
                run_id=f"record-{scenario}-{system}",
            )
        )
        console.print(f"recorded {cassette} ({outcome.report.status})")


if __name__ == "__main__":
    app()
