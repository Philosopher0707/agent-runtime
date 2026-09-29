SHELL := /bin/bash

# `uv` resolves the interpreter from `.python-version` (3.13). Do not pin UV_PYTHON here:
# a default of `command -v python3` looks harmless locally and breaks CI, where the
# runner's python3 may be older than the project requires. Set UV_PYTHON in the
# environment to override.
UV ?= uv

.PHONY: help install lock-check check lint format test eval live smoke markers ci rollback register notes run clean

help:
	@echo "install     sync dependencies into .venv (creates uv.lock)"
	@echo "ci          every gate, in order, stopping at the first failure  <- what CI runs"
	@echo "check       ruff + pytest            <- run before every commit"
	@echo "eval        golden-set run, prints score, non-zero below threshold"
	@echo "live        property suite against a REAL model (needs AGENT_API_KEY; not in ci)"
	@echo "markers     measure the injection-marker rule (ARGS=--repo sweeps this repo)"
	@echo "smoke       boot the service, POST one run, assert 200 + output schema"
	@echo "lock-check  fail if uv.lock is out of date with pyproject.toml"
	@echo "rollback    revert back to REV=<sha> on a branch, through the gates"
	@echo "register    regenerate REGISTER.md — the suite, numbered and by category"
	@echo "notes       regenerate NOTES.md — the docs, with the claim each one leads with"
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

# Not part of `ci`: this calls a real model, so it needs a key and it is not deterministic.
# The CI-safe half of the same question is `tests/test_recorded_runs.py`, which replays
# recorded real traces with no key and no network.
live:
	$(UV) run python -m evals.live $(ARGS)

markers:
	$(UV) run python scripts/measure_markers.py $(ARGS)

smoke:
	$(UV) run python scripts/smoke.py

rollback:
	$(UV) run python scripts/rollback.py --to $(REV) $(ARGS)

# REGISTER.md is a projection of the suite, so it is regenerated rather than edited. It is not a
# gate: the gate is tests/test_register.py, which fails when the committed page and the suite
# disagree — so a stale register fails `make check`, not a sixth step here.
register:
	$(UV) run python -m tests.register --write

# NOTES.md is a projection of the docs tree for the same reason, and is guarded the same way by
# tests/test_notes.py: a missing document, a quotation that is no longer verbatim, or a page that does
# not match a fresh render all fail `make check`.
notes:
	$(UV) run python scripts/derive_notes.py --write

run:
	$(UV) run python cli.py --config configs/default.yaml --task "What is 21 * 2?"

clean:
	rm -rf .pytest_cache .ruff_cache .traces dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
