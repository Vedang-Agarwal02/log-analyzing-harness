# Incident investigator

A multi-agent system that investigates production incidents. Six cooperating LLM agents (Claude by default, or Gemini) query logs, metrics, deployments, config changes, service topology and incident history. They argue over competing hypotheses and produce a report with ranked root causes, supporting and contradicting evidence, confidence and recommended actions. When the evidence is not enough, the answer is an honest "inconclusive".

It runs against a simulated retail-investing platform with 12 generated incidents that include misleading evidence, conflicting signals, tool failures and a prompt injection. An automated evaluation compares it with a single-agent baseline on the same model, tools and budget.

## Quickstart

You need [uv](https://docs.astral.sh/uv/) (it installs Python 3.14 for you). Real runs also need an API key for the model's provider: Anthropic for `claude-*` models, the Gemini API for `gemini-*` models.

```bash
uv sync                               # install dependencies into .venv
uv run pytest                         # offline test suite, no API key needed
uv run investigator list-scenarios
```

Run one investigation. This calls the real API and costs about $1–2 on Claude Opus 5.5:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or: set -a; source .env; set +a
uv run investigator investigate --scenario INC-02 --system multi
```

To run on Gemini instead, export `GEMINI_API_KEY` and add `--model gemini-4-argon` (or another `gemini-*` id, such as the cheaper `gemini-3.8-flash`).

The command prints the status, the top hypothesis, token use and the path of `runs/<run-id>/report.md`. To see what every agent and tool did, run `uv run investigator show-trace runs/<run-id>`.

Replay a recorded run offline, with no key and no cost. Cassettes are stored per model in `tests/cassettes/<model id>/`, and the replay must use the model the recording was made with:

```bash
uv run investigator investigate --scenario INC-01 --system single --model gemini-3.8-flash --replay tests/cassettes/gemini-3.8-flash/INC-01-single.json
```

## Commands

| Command | What it does |
|---|---|
| `investigator generate-data` | Regenerates `datasets/generated/` from `datasets/scenarios/*.yaml` (deterministic) |
| `investigator list-scenarios` | Lists the scenarios and their titles |
| `investigator investigate --scenario INC-01 --system multi` | Runs one investigation (`--system single` for the baseline). Options: `--model`, `--config`, `--record FILE`, `--replay FILE`, `--no-critic` |
| `investigator show-trace RUN_DIR` | Prints a run's trace as a tree of phases, LLM calls and tool calls |
| `investigator eval` | Runs the evaluation. Options: `--scenarios`, `--systems`, `--trials`, `--concurrency`, `--model`, `--out`, `--config`, `--yes`, `--no-critic`, `--min-accuracy` |
| `investigator summarize RESULTS_DIR` | Rebuilds `results.md` and `summary.json` from `results.jsonl` |
| `investigator record-cassettes` | Records real responses for the offline replay tests (`--model`, `--out-dir tests/cassettes/<model id>`) |
| `investigator ui` | Opens the demo UI in your browser (see below) |

Make targets: `make setup`, `make data`, `make test`, `make lint`, `make format`, `make typecheck`, `make check`, `make run` (e.g. `make run SCENARIO=INC-02 SYSTEM=single`), `make ui`, `make eval-smoke`, `make eval`, `make record` (needs `MODEL`, e.g. `make record MODEL=gemini-3.8-flash`).

## Configuration

Every setting is in `config/default.yaml`. Use `--config my.yaml` to load another file (keys it leaves out fall back to built-in defaults) and `--model` to change the model for one command.

| Setting | Default | Meaning |
|---|---|---|
| `llm.model` | `claude-opus-5-5` | Model for every agent and for the baseline. `claude-*` ids run on Claude, `gemini-*` ids on Gemini |
| `llm.refusal_fallback` | `true` | Claude only: if the model refuses, retry on the server-side fallback model (beta API) |
| `llm.prompt_caching` | `true` | Claude only: cache system prompts and transcripts (Gemini caches automatically) |
| `llm.gemini_tool_mode` | `AUTO` | Gemini only: `VALIDATED` constrains function calls to their schemas |
| `investigation.max_rounds` | `3` | Critic feedback rounds |
| `investigation.max_parallel_tasks` | `4` | Specialists running at the same time |
| `investigation.critic_enabled` | `true` | `--no-critic` turns the critic loop off (ablation) |
| `budget.max_total_tokens` | `1500000` | Shared by all agents in a run; `budget.reserve_tokens` (150000) is kept for the reporter |
| `tools.default_timeout_s`, `tools.max_attempts` | `8`, `3` | Per tool call |
| `evaluation.trials`, `evaluation.concurrency` | `3`, `4` | Repetitions per scenario and system, and runs in parallel |

Agents (prompt, tools, effort, turn limits, and an optional per-agent `model`) are defined in `config/agents.yaml`, and their prompts in `prompts/`. Giving agents models from different providers mixes Claude and Gemini in one run.

Environment variables: `ANTHROPIC_API_KEY` for Claude models, and `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) for Gemini models. Only the keys for the providers you use are needed.

## How it works

1. **Plan.** A planner agent turns the incident into a small task graph.
2. **Gather.** Observability (logs and metrics) and dependency (deployments, config, topology) specialists run in parallel, each with only its own tools. Every tool result mints evidence IDs.
3. **Reason.** A reasoning agent ranks 2–5 hypotheses, citing supporting and contradicting evidence.
4. **Critique.** Code checks the hypotheses (timeline, thin support, duplicates). A critic then accepts them, asks for a revision, or requests more evidence, which becomes new parallel tasks, for up to 3 rounds.
5. **Report.** A reporter writes the report. Code validates every citation and derives the status from the confidence.

Diagrams and details: [docs/architecture.md](docs/architecture.md). Dataset: [docs/dataset.md](docs/dataset.md).

## Evaluation

```bash
make eval-smoke    # 2 scenarios x 2 systems x 1 trial, about $5 on Opus 5.5, under $1 on gemini-3.8-flash
make eval          # 12 scenarios x 2 systems x 3 trials, about $100 on Opus 5.5 or $75 on gemini-4-argon (asks first)
```

Results go to `evals/results/<timestamp>-<model>/`:
- `results.jsonl`: one row per run.
- `errors.jsonl`: infrastructure failures, which are never scored.
- `summary.json` and `results.md`.

An interrupted evaluation resumes when you pass the same `--out`. For cheaper development runs add `--model claude-haiku-5-5` (under $5 for the full suite) or `--model gemini-3.8-flash`. To compare providers, run the same evaluation again with another `--model` (for example `gemini-4-argon`) and its own `--out`.

Quality metrics:
- Root-cause accuracy (top-1 and top-3) and trigger accuracy
- Status correctness
- Citation validity and key-evidence recall
- Red-herring avoidance and data-gap acknowledgement
- Prompt-injection resistance
- Brier score

Efficiency metrics (per run): LLM calls, tool calls, tokens, cost and wall-clock.

The final results and their interpretation: [docs/technical-note.md](docs/technical-note.md) and `evals/results/final-gemini-3.8-flash/results.md`.

## Testing

| Suite | Command | Covers |
|---|---|---|
| Unit | `uv run pytest tests/unit` | Domain models, generator, harness, tools, validators, policies, scoring, pricing |
| Integration | `uv run pytest tests/integration` | Full investigations with a scripted model (parallelism, feedback rounds, failures, budgets), CLI, eval runner, cassette replay |
| Architecture | `uv run pytest tests/architecture` | Layering, no ground-truth leakage, docs, CI files, deliverables |
| Live | `uv run pytest -m live` | Real API smoke tests: Claude (about $2, needs `ANTHROPIC_API_KEY`) and Gemini (under $1, needs `GEMINI_API_KEY`; `GEMINI_SMOKE_MODEL` picks the model) |

CI (`.github/workflows/ci.yml`) runs on every push and pull request. It checks lint and types, confirms the dataset is reproducible, and runs the offline suite. `.github/workflows/eval.yml` runs a paid evaluation on demand. It needs the `ANTHROPIC_API_KEY` repository secret for Claude models, or `GEMINI_API_KEY` for Gemini models.

## Demo UI

A plain web page for demos: `uv run investigator ui` (or `make ui`) opens http://localhost:8501. It has three tabs:
- **Run an incident:** run a built-in scenario, or type a custom incident with a few log lines of your own. The custom incident is investigated against a built-in scenario's telemetry. Choose multi or single agent and the model, then follow the run live.
- **Runs and files:** every run under `runs/` and `evals/results/`. For each run you get the report, a Problems list (failed tool calls, LLM errors, failed tasks, fallbacks), the full timeline and every file the run wrote.
- **Evaluations:** each evaluation's results tables and infrastructure errors.

Runs started from the page go through the same `investigator investigate` command as the terminal, so the page needs the same API keys in the shell that started it.

## Docker

```bash
docker build --build-arg UV_VERSION=$(uv --version | cut -d' ' -f2) -t incident-investigator .
docker run --rm incident-investigator list-scenarios
docker run --rm -e ANTHROPIC_API_KEY -e GEMINI_API_KEY -v "$PWD/runs:/app/runs" incident-investigator investigate --scenario INC-02
```

On Linux, add `--user "$(id -u):$(id -g)"` to `docker run` so the files written to `runs/` belong to you.

## Extending

| To add | Do this |
|---|---|
| A tool | Write an async function with a Pydantic input model and decorate it with `@tool` (see `src/investigator/tools/`). Add it to `build_registry`, and list its name under the agent's `tools:` in `config/agents.yaml` |
| A telemetry backend | Implement a port from `src/investigator/backends/base.py` (for example a Loki `LogStore`) and pass it to `build_registry` |
| A specialist agent | Add an entry under `specialists:` in `config/agents.yaml` and its prompt in `prompts/`. The planner sees it in its catalog |
| An LLM provider | Implement `LLMClient` from `src/investigator/harness/llm.py` (see `src/investigator/harness/gemini_client.py`), and add its model-id prefix to `PROVIDER_PREFIXES` in `src/investigator/harness/routing.py` |
| A scenario | Add `datasets/scenarios/INC-13.yaml` and run `make data`. See [docs/dataset.md](docs/dataset.md) |

## Limitations and incomplete functionality

- **Simulated data.** Telemetry comes from 12 generated scenarios on one fictional platform. No connectors to real systems (Loki, Prometheus, ArgoCD, Backstage) exist; the backend ports are where they would plug in.
- **No service API.** There is no alert webhook or HTTP API. The demo UI is a local page for one person, not a multi-user service.
- **Keyword history search.** Incident-history search uses keywords, not embeddings.
- **Single process, in-memory state.** A crashed investigation is not resumed. The eval runner resumes only at run granularity.
- **Small sample.** The final evaluation has 36 runs per system, so accuracy differences below about 17 percentage points are within noise. All results are for one model.
- **Non-deterministic outputs.** Model output varies between runs. Only replays of recorded cassettes are exactly reproducible.
- **Hard-coded prices.** Token prices in `src/investigator/evaluation/pricing.py` are hard-coded (October 2026) and must be updated when prices change. Google has announced Gemini increases for 2026-12-01 and 2027-01-01.
- **Beta fallback.** The refusal fallback (Claude only) uses a beta API (`server-side-fallback-2026-07-01`). Set `llm.refusal_fallback: false` if your account does not have it. Gemini has no server-side fallback, so a Gemini refusal fails that agent run.
- **Prompts tuned on Claude.** The prompts, effort levels and budgets were written for Claude. Gemini runs work but are not tuned; the evaluation measures the difference.
- **Claude path not run live.** The Anthropic client is covered by offline tests with a fake SDK, but this build was exercised against a real API on Gemini only. Run `uv run pytest -m live` with an `ANTHROPIC_API_KEY` before relying on a Claude evaluation.
- **Gemini API choice.** The Gemini adapter uses `generateContent`, which Google keeps fully supported. Google's newer Interactions API is not used.

## External dependencies

| Dependency | Needed for | Notes |
|---|---|---|
| Anthropic API (`claude-opus-5-5`) | Real investigations and evaluations on Claude | `ANTHROPIC_API_KEY`; costs money; offline tests never call it |
| Gemini API (e.g. `gemini-4-argon`) | Real investigations and evaluations on Gemini | `GEMINI_API_KEY`; costs money; offline tests never call it |
| Python 3.14 and uv | Everything | `uv sync` installs the versions pinned in `uv.lock` |
| `anthropic`, `google-genai`, `pydantic`, `pyyaml`, `typer`, `rich` | Runtime | Versions in `pyproject.toml` |
| Docker (optional) | The container image | — |
| GitHub Actions (optional) | CI and on-demand evaluations | — |

## Project layout

```text
config/            default.yaml (settings), agents.yaml (agent catalog)
prompts/           one system prompt per agent + shared rules
datasets/          platform.yaml, scenarios/*.yaml, generated/ (committed)
src/investigator/  domain, datagen, harness, backends, tools, agents, orchestrator, baseline, reporting, evaluation, cli
tests/             unit, integration, architecture, live, cassettes
evals/results/     evaluation outputs
docs/              architecture, dataset, technical note, example reports, transcripts
```

## Deliverables

| Deliverable | Where |
|---|---|
| Runnable code and setup | This README, `src/`, `Makefile`, `Dockerfile` |
| Architecture diagram | [docs/architecture.md](docs/architecture.md) |
| Dataset and tests | `datasets/`, [docs/dataset.md](docs/dataset.md), `tests/` |
| Evaluation results | `evals/results/final-gemini-3.8-flash/results.md` |
| Technical note (≤ 500 words) | [docs/technical-note.md](docs/technical-note.md) |
| Example reports | `docs/examples/` |
| Coding-assistant transcripts | `docs/transcripts/` |
