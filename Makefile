.PHONY: setup data test lint format typecheck check run ui eval-smoke eval record

setup:
	uv sync

data:
	uv run investigator generate-data

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

check: lint typecheck test

run:
	uv run investigator investigate --scenario $(or $(SCENARIO),INC-01) --system $(or $(SYSTEM),multi)

ui:
	uv run investigator ui

eval-smoke:
	uv run investigator eval --scenarios INC-01,INC-10 --trials 1 --yes

eval:
	uv run investigator eval --trials 3

record:
	uv run investigator record-cassettes --scenario INC-01 --model $(MODEL) --out-dir tests/cassettes/$(MODEL)
