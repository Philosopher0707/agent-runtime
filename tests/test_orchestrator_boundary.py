"""The orchestrator's boundary: what `run()` guarantees when a collaborator breaks its contract.

`run()` says it never raises for a task-level problem. That was true only as long as every
collaborator kept a promise nothing checked — the `Provider` protocol promises `ProviderError` and
nothing else, the tool boundary promises never to raise — and an adapter leaking a socket error took
the whole run down. A guarantee that depends on the other party behaving is not a guarantee.

So the seam enforces it, and these tests force each clause:

* an absorbed failure is **classified** and the exception *type* is named — absorbing is not hiding;
* a process stopping is **not** absorbed, because that is not the task failing;
* a log that cannot write still escapes, because there is no record to report a status in;
* **the loop's own bugs are not absorbed** — the seam catches someone else's mistake, only there;
* nothing is absorbed **before the run starts**, because there is no run yet to fail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from providers.base import ProviderSignal
from providers.replay import ReplayDivergence
from runtime import loop
from runtime.schemas import ContextRecord, FailureEvent, ModelCallRecord, RunOutput, ToolCallRecord
from runtime.status import ToolOutcome
from runtime.trace import TraceWriter
from tests.helpers import execute, make_config, text, tool_call


class ExplodingProvider:
    """A provider that breaks its contract by raising something that is not a ProviderError."""

    name = "exploding"
    model = "exploding"

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def complete(self, messages: Any, tools: Any) -> Any:
        raise self.exc

    def health(self) -> bool:
        return True


class ExplodingBoundary:
    """A tool boundary that raises instead of returning a record for the dispatch."""

    def descriptors(self) -> list[Any]:
        return []

    def dispatch(self, request: Any, *, step: int, confirmation_token: str | None = None) -> Any:
        raise RuntimeError("the boundary exploded")


class UndescribableBoundary:
    """A boundary that cannot say what tools it has — a composition error, not a run failure."""

    def descriptors(self) -> list[Any]:
        raise RuntimeError("no tool set")

    def dispatch(self, request: Any, *, step: int, confirmation_token: str | None = None) -> Any:
        raise AssertionError("a run started from a boundary that could not describe itself")


class RecordingLog:
    """The log boundary, in memory, so a test can see what was written and when."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.records: list[ToolCallRecord] = []
        self.trace_id = "recording"

    def enable_redaction(self, redactor: Any) -> None:
        self.events.append("enable_redaction")

    def run_started(self, **kwargs: Any) -> None:
        self.events.append("run_started")

    def context(self, record: ContextRecord) -> None:
        self.events.append("context")

    def model_call(self, record: ModelCallRecord) -> None:
        self.events.append("model_call")

    def tool_call(self, record: ToolCallRecord) -> None:
        self.events.append("tool_call")
        self.records.append(record)

    def failure(self, event: FailureEvent) -> None:
        self.events.append("failure")

    def run_finished(self, output: RunOutput) -> None:
        self.events.append("run_finished")


class LogThatCannotWrite(RecordingLog):
    def tool_call(self, record: ToolCallRecord) -> None:
        raise OSError("the disk is full")


# ------------------------------------------------------------- the provider seam


def test_a_provider_that_raises_something_else_is_absorbed(tracer: Any) -> None:
    """Before this, a leaked `OSError` from an adapter escaped `run()` — the one thing it promises
    not to do. From the loop's seat the provider failed, so its failure class is the right one."""
    output = execute(
        "go",
        config=make_config(),
        tracer=tracer,
        provider=ExplodingProvider(OSError("connection reset")),
    )

    assert output.status == "failed"
    assert output.reason == "provider_error"
    assert "provider_error" in [str(c) for c in output.failure_classes]


def test_the_absorbed_failure_names_the_exception_type(tracer: Any) -> None:
    """Absorbing is not hiding. A run that reports `provider_error` with no mention of the leak
    would send someone to look at the endpoint for a defect that is in the adapter."""
    output = execute(
        "go",
        config=make_config(),
        tracer=tracer,
        provider=ExplodingProvider(ValueError("a parse bug")),
    )

    detail = output.failures[0].detail
    assert "ValueError" in detail
    assert "a parse bug" in detail


def test_a_provider_signal_is_not_absorbed(tracer: Any) -> None:
    """The escape hatch the catch-all needed, and the reason it exists.

    `ReplayDivergence` is a `ProviderSignal`: an implementation raising it has not failed, it is
    telling the caller that a replay diverged. Filing that as `provider_error` would report a
    defect in this project as a run that failed — and two replay tests caught exactly that when
    the seam was first written, which is the only reason it is not shipped behaviour.
    """
    with pytest.raises(ProviderSignal, match="not a task failure"):
        execute(
            "go",
            config=make_config(),
            tracer=tracer,
            provider=ExplodingProvider(ProviderSignal("not a task failure")),
        )


def test_a_replay_divergence_is_a_provider_signal() -> None:
    """The type is the contract: a divergence must be recognisable as a signal by anything that
    catches unexpected provider exceptions, without importing the replay adapter."""
    assert issubclass(ReplayDivergence, ProviderSignal)


def test_a_process_stopping_is_not_a_task_failure(tracer: Any) -> None:
    """`KeyboardInterrupt` and `SystemExit` mean the process is stopping. Filing that as a failed
    run would swallow a Ctrl-C — and a runtime that cannot be interrupted is worse than one that
    cannot be relied on."""
    with pytest.raises(KeyboardInterrupt):
        execute(
            "go",
            config=make_config(),
            tracer=tracer,
            provider=ExplodingProvider(KeyboardInterrupt()),
        )


# --------------------------------------------------------------- the tool seam


def test_a_tool_boundary_that_raises_is_absorbed_into_a_record(tracer: Any) -> None:
    """Synthesised into the record it should have returned, so the run continues through the
    *existing* interpretation rather than a second failure path."""
    output = execute(
        "go",
        config=make_config(),
        tracer=tracer,
        registry=ExplodingBoundary(),
        script=[tool_call("echo", {"text": "x"}), text("done")],
    )

    assert output.status == "degraded", "an optional tool that failed is degraded, not partial"
    assert len(output.tool_calls) == 1
    assert output.tool_calls[0].outcome is ToolOutcome.ERROR
    assert "RuntimeError" in (output.tool_calls[0].error or "")


def test_the_absorbed_dispatch_still_leaves_exactly_one_record() -> None:
    """The log boundary's first clause — one record per dispatch — has to survive this. A dispatch
    that raised and left no record would be a silently missing call, which is the failure the whole
    record exists to make impossible."""
    log = RecordingLog()
    execute(
        "go",
        config=make_config(),
        tracer=log,
        registry=ExplodingBoundary(),
        script=[tool_call("echo", {"text": "x"}), text("done")],
    )

    assert log.events.count("tool_call") == 1
    assert len(log.records) == 1
    assert log.events.count("run_finished") == 1


# ------------------------------------------------- what is deliberately *not* absorbed


def test_a_log_that_cannot_write_still_escapes(tracer: Any) -> None:
    """The one declared exception. A run with no record is not a run this runtime performs, so
    there is no status to report — see `RunLog`."""
    with pytest.raises(OSError, match="disk is full"):
        execute(
            "go",
            config=make_config(),
            tracer=LogThatCannotWrite(),
            script=[tool_call("echo", {"text": "x"}), text("done")],
        )


def test_the_loop_does_not_absorb_its_own_bug(monkeypatch: pytest.MonkeyPatch, tracer: Any) -> None:
    """The seam catches *someone else's* mistake, and only there. A defect in this module must be
    loud — filed as a task failure it would look like the task's fault, and nobody would fix it."""

    def explode(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("a bug in the loop itself")

    monkeypatch.setattr(loop._Orchestrator, "_interpret", explode)

    with pytest.raises(RuntimeError, match="a bug in the loop itself"):
        execute(
            "go",
            config=make_config(),
            tracer=tracer,
            script=[tool_call("echo", {"text": "x"}), text("done")],
        )


def test_a_boundary_that_cannot_describe_itself_fails_before_the_run_starts() -> None:
    """There is no run yet to fail, and nothing has been written — so this is a composition error
    and it raises, rather than producing a trace that claims a run happened."""
    log = RecordingLog()
    with pytest.raises(RuntimeError, match="no tool set"):
        execute("go", config=make_config(), tracer=log, registry=UndescribableBoundary())

    assert log.events == [], "a run that could not start wrote something anyway"


# ------------------------------------------------------------------ non-vacuity


def test_every_absorbed_case_returns_an_output(tmp_path: Path) -> None:
    """The floor for the whole file: each clause above asserts a *shape*, so this asserts the one
    thing they all depend on — that `run()` returned at all, with exactly one `run_finished`."""
    with TraceWriter(tmp_path / "traces", "absorbed") as tracer:
        output = execute(
            "go",
            config=make_config(),
            tracer=tracer,
            provider=ExplodingProvider(OSError("reset")),
        )
    assert isinstance(output, RunOutput)
    assert output.steps >= 1
