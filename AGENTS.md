# Rules for coding agents working in this repository

## What this is
A multi-agent incident investigation system (Python 3.14, uv). Start with `README.md` and `docs/architecture.md`. The design rationale and requirements traceability are in `docs/design/01-strategy-and-architecture.md`.

## Commands
- Install: `uv sync`
- Everything (lint, types, tests): `make check`
- Tests (offline, no API key): `uv run pytest`
- Lint/format: `uv run ruff format . && uv run ruff check --fix .`
- Types: `uv run mypy`
- Regenerate datasets: `uv run investigator generate-data`

## Hard rules
1. Never put scenario-specific logic, service names from scenarios, change IDs or scenario IDs in `src/` (outside `src/investigator/datagen/`) or in `prompts/`.
2. Agent, tool, harness and orchestrator code must never import `investigator.evaluation` or `investigator.datagen`, and must never read `ground_truth.json`.
3. Never edit message history in the agent loop: transcripts are append-only.
4. Keep everything that reaches a prompt deterministic: sorted JSON, sorted collections, no timestamps or UUIDs.
5. Tests never call a real API. Use the scripted LLM or a recorded cassette.
6. Do not change test assertions to make tests pass. Fix the code.
7. After each change: format, lint, type-check and test (`make check`).
8. When you are blocked, stop and say so. Never work around a failing test.
