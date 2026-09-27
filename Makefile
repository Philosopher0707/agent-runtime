SHELL := /bin/bash

# `uv` resolves the interpreter from `.python-version` (3.13). Do not pin UV_PYTHON here:
# a default of `command -v python3` looks harmless locally and breaks CI, where the
# runner's python3 may be older than the project requires. Set UV_PYTHON in the
# environment to override.
UV ?= uv

.PHONY: help install lock-check check lint format test eval smoke markers ci rollback run clean

help:
	@echo "install     sync dependencies into .venv (creates uv.lock)"
	@echo "ci          every gate, in order, stopping at the first failure  <- what CI runs"
	@echo "check       ruff + pytest            <- run before every commit"
	@echo "eval        golden-set run, prints score, non-zero below threshold"
	@echo "markers     measure the injection-marker rule (ARGS=--repo sweeps this repo)"
	@echo "smoke       boot the service, POST one run, assert 200 + output schema"
	@echo "lock-check  fail if uv.lock is out of date with pyproject.toml"
	@echo "rollback    revert back to REV=<sha> on a branch, through the gates"
	@echo "run         run the CLI against configs/default.yaml"

install:
	$(UV) sync --all-extras

# The gates, in dependency order and stopping at the first failure. This recipe is the
# single definition of "the gates": `.github/workflows/ci.yml` calls `make ci` and runs
# nothing else, so CI and a local run cannot drift. `tests/test_ci_contract.py` parses
# this recipe and fails if a gate is dropped or a target is misspelled.
ci:
	@$(MAKE) --no-print-directory lock-check
	@$(MAKE) --no-print-directory check
	@$(MAKE) --no-print-directory eval
	@$(MAKE) --no-print-directory markers
	@$(MAKE) --no-print-directory smoke
	@echo ""
	@echo "ci: all gates passed"

lock-check:
	$(UV) lock --check

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

markers:
	$(UV) run python scripts/measure_markers.py $(ARGS)

smoke:
	$(UV) run python scripts/smoke.py

rollback:
	$(UV) run python scripts/rollback.py --to $(REV) $(ARGS)

run:
	$(UV) run python cli.py --config configs/default.yaml --task "What is 21 * 2?"

clean:
	rm -rf .pytest_cache .ruff_cache .traces dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
