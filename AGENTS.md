# Rules for coding agents working in this repository

## What this is
A multi-agent incident investigation system (Python 3.14, uv). Read `docs/architecture.md` once it exists.
The build is driven by `docs/design/02-development-plan.md`. Follow it task by task.

## Every session
1. If `ERRORS.md` exists, read it before anything else.
2. Follow `docs/design/00-execution-guide.md`: session routine, when to stop, and how to record a blocker in `ERRORS.md`.

## Commands
- Install: `uv sync`
- Tests (offline, no API key): `uv run pytest`
- Lint/format: `uv run ruff format . && uv run ruff check --fix .`
- Types: `uv run mypy`
- Regenerate datasets: `uv run investigator generate-data`

## Hard rules
1. Never put scenario-specific logic, service names from scenarios, change IDs or scenario IDs in `src/` (outside `src/investigator/datagen/`) or in `prompts/`.
2. Agent, tool, harness and orchestrator code must never import `investigator.evaluation` or `investigator.datagen`, and must never read `ground_truth.json`.
3. Never edit message history in the agent loop: transcripts are append-only.
4. Keep everything that reaches a prompt deterministic: sorted JSON, sorted collections, no timestamps or UUIDs.
5. Never call the real API from tests unless the test is marked `@pytest.mark.live`.
6. Do not change test assertions to make tests pass. Fix the code.
7. After each task: format, lint, test, update `PROGRESS.md`, commit.
8. When you are blocked, record it in `ERRORS.md` and stop. Never work around a failing test.
