SHELL := /bin/bash

# The managed interpreter lives outside the repo; pin uv to it so `uv sync` does not
# download a second copy of the same Python. Override with UV_PYTHON=... if needed.
UV_PYTHON ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3)
export UV_PYTHON

UV ?= uv

.PHONY: help install check lint format test eval smoke markers run clean

help:
	@echo "install  sync dependencies into .venv (creates uv.lock)"
	@echo "check    ruff + pytest            <- run before every commit"
	@echo "eval     golden-set run, prints score, non-zero below threshold"
	@echo "smoke    boot the service, POST one run, assert 200 + output schema"
	@echo "markers  measure the injection-marker rule (ARGS=--repo to sweep this repo)"
	@echo "run      run the CLI against configs/default.yaml"

install:
	$(UV) sync --all-extras

check: lint test

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

test:
	$(UV) run pytest

eval:
	$(UV) run python -m evals.score

smoke:
	$(UV) run python scripts/smoke.py

markers:
	$(UV) run python scripts/measure_markers.py $(ARGS)

run:
	$(UV) run python cli.py --config configs/default.yaml --task "What is 21 * 2?"

clean:
	rm -rf .pytest_cache .ruff_cache .traces dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
