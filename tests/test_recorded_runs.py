"""Real runs, replayed. The half of the live suite that can run in CI.

A live suite needs an API key and is non-deterministic, so it cannot gate a push. But a
*recorded* real run is neither of those things, and replaying one answers the question that
matters for the runtime: **does it handle what a real model actually produced?**

These fixtures are traces from real OpenRouter runs, committed to the repository. Replaying
one asserts the canonical projection is identical to the recorded one — same status, same
tool outcomes, same prompt hashes, same accounting.

Promotion path: run `make live`, read the trace it wrote, copy it into `evals/fixtures/`, and
CI covers that behaviour forever without a key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.replay import replay
from runtime.schemas import TRACE_SCHEMA_VERSION, RunOutput
from runtime.trace import read_trace, recorded_task

FIXTURES = Path(__file__).resolve().parent.parent / "evals" / "fixtures"


def fixtures() -> list[Path]:
    return sorted(FIXTURES.glob("*.jsonl"))


def recorded_output(path: Path) -> RunOutput:
    finished = read_trace(path).first("run_finished")
    assert finished is not None
    return RunOutput.model_validate(finished.payload)


@pytest.mark.parametrize("path", fixtures(), ids=lambda path: path.stem)
def test_a_recorded_real_run_replays_identically(path: Path, tmp_path: Path) -> None:
    """The strongest claim in the project, checked against a real model's output."""
    replayed = replay(path, trace_dir=tmp_path / "replay")
    assert replayed.canonical() == recorded_output(path).canonical()


@pytest.mark.parametrize("path", fixtures(), ids=lambda path: path.stem)
def test_a_recorded_run_rebuilds_the_same_prompts(path: Path, tmp_path: Path) -> None:
    """Prompt hashes are inside the canonical projection, but asserted separately too — a
    change to `canonical()` should not silently stop checking them."""
    replayed = replay(path, trace_dir=tmp_path / "replay")
    assert replayed.prompt_hashes == recorded_output(path).prompt_hashes


@pytest.mark.parametrize("path", fixtures(), ids=lambda path: path.stem)
def test_a_recorded_run_is_readable(path: Path) -> None:
    trace = read_trace(path)
    assert trace.trace_id
    assert trace.first("run_started") is not None
    assert trace.first("run_finished") is not None
    assert recorded_task(trace)


@pytest.mark.parametrize("path", fixtures(), ids=lambda path: path.stem)
def test_a_fixture_carries_a_known_schema_version(path: Path) -> None:
    """These were written before the field existed, so they are version 1 by definition —
    which is exactly why the field has a default rather than being required."""
    for event in read_trace(path).events:
        assert event.schema_version == TRACE_SCHEMA_VERSION


# --------------------------------------------------------------- non-vacuity


def test_there_are_fixtures_to_replay() -> None:
    assert len(fixtures()) >= 3, "the fixture set has shrunk — check whether that was deliberate"


def test_the_fixtures_cover_a_tool_call() -> None:
    """A fixture set of plain answers would not exercise the tool path at all."""
    with_tools = [p for p in fixtures() if recorded_output(p).tool_calls]
    assert with_tools, "no fixture exercises a tool call"


def test_the_fixtures_cover_more_than_one_step() -> None:
    """A single-step fixture cannot show that the transcript accumulates correctly."""
    multi = [p for p in fixtures() if recorded_output(p).steps > 1]
    assert multi, "no fixture takes more than one step"


def test_the_fixtures_came_from_a_real_model() -> None:
    """If these were stub runs the whole point would be lost — and it would be invisible."""
    for path in fixtures():
        started = read_trace(path).first("run_started")
        assert started is not None
        assert started.payload["model"] != "stub-mid-tier", f"{path.stem} is a stub run"
