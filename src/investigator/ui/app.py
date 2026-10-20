"""Demo UI: start investigations, follow them live, and browse every file and failure they produce.

Start it with `uv run investigator ui`. Everything shown is read from files the harness writes, and runs
are started through the same CLI you use in a terminal.
"""

import subprocess
from pathlib import Path

import pydantic
import streamlit as st

from investigator.backends.simulated import load_services
from investigator.domain.common import iso
from investigator.domain.incident import IncidentReport
from investigator.runtime import list_scenarios, load_incident, new_run_id
from investigator.settings import load_settings, project_root
from investigator.ui import data

st.set_page_config(page_title="Incident investigator", layout="wide")

settings = load_settings()
# Tests (and anyone curious) can point the UI at other folders through session state.
runs_root = Path(st.session_state.get("runs_root") or settings.path(settings.runs_dir))
evals_root = Path(st.session_state.get("evals_root") or settings.path("evals/results"))
datasets_root = Path(st.session_state.get("datasets_dir") or settings.path(settings.datasets_dir))
settings = settings.model_copy(update={"datasets_dir": str(datasets_root)})
SEVERITIES = ["SEV1", "SEV2", "SEV3"]


def show_file(path: Path) -> None:
    kind = data.file_kind(path)
    if kind == "markdown":
        st.markdown(path.read_text(encoding="utf-8"))
    elif kind == "json":
        st.json(data.read_json(path), expanded=False)
    elif kind == "jsonl":
        st.dataframe(data.flatten(data.read_jsonl(path)))
    else:
        st.code(path.read_text(encoding="utf-8", errors="replace")[:20000])


def show_run(run: data.RunInfo) -> None:
    columns = st.columns(6)
    columns[0].metric("Status", run.status)
    columns[1].metric("System", run.system)
    columns[2].metric("LLM calls", run.llm_calls)
    columns[3].metric("Tool calls", run.tool_calls)
    columns[4].metric("Problems", run.problems)
    columns[5].metric("Wall-clock (s)", f"{run.wall_s:.0f}")
    st.caption(
        f"{run.scenario} · model {run.model} · termination: {run.termination or 'n/a'} · {run.path}"
    )
    events = data.read_jsonl(run.path / "trace.jsonl")
    report_tab, problems_tab, timeline_tab, files_tab = st.tabs(
        ["Report", f"Problems ({run.problems})", "Timeline", "Files"]
    )
    with report_tab:
        report = run.path / "report.md"
        if report.exists():
            st.markdown(report.read_text(encoding="utf-8"))
        else:
            st.info(
                "No report yet: the run is still going or stopped early. See Timeline and Problems."
            )
    with problems_tab:
        rows = data.problems(events)
        if rows:
            st.dataframe(rows)
        else:
            st.success("Nothing failed in this run.")
    with timeline_tab:
        rows = data.timeline(events)
        kinds = sorted({row["type"] for row in rows})
        chosen = st.multiselect("Event types", kinds, default=kinds, key=f"kinds-{run.run_id}")
        st.dataframe([row for row in rows if row["type"] in chosen][: data.MAX_ROWS])
    with files_tab:
        for path in data.run_files(run.path):
            with st.expander(f"{path.name} ({path.stat().st_size:,} bytes)"):
                show_file(path)


def start_run(command: list[str], run_id: str) -> None:
    log_dir = runs_root / "_ui"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{run_id}.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, cwd=project_root(), stdout=log, stderr=subprocess.STDOUT
        )
    st.session_state["active"] = {"run_id": run_id, "process": process, "log": str(log_path)}


@st.fragment(run_every=2)
def live_panel() -> None:
    active = st.session_state.get("active")
    if not active:
        return
    code = active["process"].poll()
    run_dir = runs_root / active["run_id"]
    events = data.read_jsonl(run_dir / "trace.jsonl")
    state = (
        "running" if code is None else ("finished" if code == 0 else f"failed (exit code {code})")
    )
    st.subheader(f"Run {active['run_id']}: {state}")
    columns = st.columns(3)
    columns[0].metric("LLM calls", sum(1 for event in events if event.get("type") == "llm_call"))
    columns[1].metric("Tool calls", sum(1 for event in events if event.get("type") == "tool_call"))
    columns[2].metric("Problems", len(data.problems(events)))
    st.dataframe(data.timeline(events)[-15:])
    if code is not None and code != 0:
        st.error("The command failed. Its output:")
        st.code(Path(active["log"]).read_text(encoding="utf-8", errors="replace")[-5000:])
    elif code == 0 and (run_dir / "report.md").exists():
        st.success("Done. Every file of this run is in the 'Runs and files' tab.")
        st.markdown((run_dir / "report.md").read_text(encoding="utf-8"))


st.title("Incident investigator")
run_tab, runs_tab, evals_tab = st.tabs(["Run an incident", "Runs and files", "Evaluations"])

with run_tab:
    scenarios = list_scenarios(settings)
    if not scenarios:
        st.warning(
            f"No scenarios under {datasets_root}. Run `uv run investigator generate-data` first."
        )
    else:
        source = st.radio(
            "Incident", ["Built-in scenario", "Custom incident"], horizontal=True, key="source"
        )
        custom = source == "Custom incident"
        label = "Telemetry and topology from scenario" if custom else "Scenario"
        base_id = st.selectbox(label, scenarios, key="base")
        base_root = datasets_root / base_id
        base = load_incident(base_root)
        system = st.radio("System", ["multi", "single"], horizontal=True, key="system")
        model = st.text_input(
            "Model",
            settings.llm.model,
            key="model",
            help=(
                "claude-* or gemini-*. The terminal that started the UI needs that provider's key or Vertex AI "
                "settings. To replay a recording, use the model it was recorded with."
            ),
        )
        if not custom:
            st.markdown(f"**{base.title}** ({base.severity}, reported {iso(base.reported_at)})")
            st.write(base.summary)
            cassette = project_root() / "tests" / "cassettes" / f"{base_id}-{system}.json"
            replay = st.checkbox(
                "Replay the recorded run (offline: no API key, no cost)",
                key="replay",
                disabled=not cassette.exists(),
            )
            if st.button("Run investigation", type="primary", key="run_builtin"):
                run_id = new_run_id(base_id, system)
                command = data.investigate_command(
                    scenario=base_id,
                    system=system,
                    run_id=run_id,
                    model=model,
                    replay=cassette if replay else None,
                )
                start_run(command, run_id)
        else:
            services = sorted(service.name for service in load_services(base_root / "telemetry"))
            window_start, window_end = data.telemetry_window(base_root)
            title = st.text_input("Title", base.title, key="title")
            summary = st.text_area("What is happening?", base.summary, key="summary")
            severity = st.selectbox(
                "Severity", SEVERITIES, index=SEVERITIES.index(base.severity), key="severity"
            )
            default_services = [name for name in base.affected_services if name in services]
            affected = st.multiselect(
                "Affected services", services, default=default_services, key="affected"
            )
            reported = st.text_input(
                "Reported at (UTC, ISO 8601)", iso(base.reported_at), key="reported"
            )
            st.caption(
                f"This scenario's telemetry covers {window_start} to {window_end}. Keep timestamps inside it."
            )
            logs_text = st.text_area(
                "Extra log lines (optional), one per line: TIMESTAMP LEVEL SERVICE message",
                placeholder=f"{iso(base.reported_at)} ERROR {services[0]} Something specific went wrong",
                key="logs",
            )
            keep_faults = st.checkbox(
                "Keep this scenario's simulated tool failures", value=True, key="keep_faults"
            )
            if st.button("Run investigation", type="primary", key="run_custom"):
                records, errors = data.parse_log_lines(logs_text, set(services))
                run_id = new_run_id("CUSTOM", system)
                try:
                    incident = IncidentReport.model_validate(
                        {
                            "incident_id": run_id,
                            "title": title,
                            "severity": severity,
                            "reported_at": reported,
                            "reporter": "demo user",
                            "summary": summary,
                            "affected_services": affected,
                        }
                    )
                except pydantic.ValidationError as exc:
                    errors.append(f"Incident fields: {exc.errors()[0]['msg']}")
                for error in errors:
                    st.error(error)
                if not errors:
                    custom_root = runs_root / "_custom" / run_id
                    data.make_custom_scenario(
                        base_root,
                        custom_root / "datasets",
                        incident,
                        records,
                        keep_faults=keep_faults,
                    )
                    config = data.write_custom_config(
                        custom_root / "config.yaml",
                        project_root() / "config" / "default.yaml",
                        custom_root / "datasets",
                    )
                    command = data.investigate_command(
                        scenario=run_id, system=system, run_id=run_id, model=model, config=config
                    )
                    start_run(command, run_id)
    live_panel()

with runs_tab:
    runs = {run.run_id: run for run in data.find_runs([runs_root, evals_root])}
    if not runs:
        st.info(
            "No runs yet. Start one in the first tab, or run `uv run investigator investigate`."
        )
    else:
        chosen_id = st.selectbox(
            "Run", list(runs), format_func=lambda run_id: runs[run_id].label, key="run_choice"
        )
        show_run(runs[chosen_id])

with evals_tab:
    evaluations = data.find_evaluations(evals_root)
    if not evaluations:
        st.info("No evaluation results yet. Run `uv run investigator eval ...` first.")
    else:
        name = st.selectbox("Evaluation", [path.name for path in evaluations], key="eval_choice")
        folder = evals_root / name
        results = folder / "results.md"
        if results.exists():
            st.markdown(results.read_text(encoding="utf-8"))
        else:
            st.info("results.md is written when the evaluation finishes.")
        failures = data.read_jsonl(folder / "errors.jsonl")
        if failures:
            st.subheader(f"Infrastructure errors ({len(failures)})")
            st.dataframe(data.flatten(failures))
        config = data.read_json(folder / "config.json")
        if config:
            with st.expander("config.json"):
                st.json(config, expanded=False)
