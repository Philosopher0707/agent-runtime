"""The log boundary: what the loop may rely on from the thing that records a run.

The obligations here were nowhere written down before this file, and the ones that matter are the
ones nothing else could catch:

* a tool-call record that is **dropped, duplicated or reordered** — the run still succeeds, and
  replay fails later, somewhere else;
* a record written **after** its result was used — a trace that omits something the model acted on;
* a log that **cannot write** and lets the run continue anyway, leaving a silent gap.

The last one is why this is a boundary and not an interface extraction: the loop's usual promise is
that it never raises for a problem in the task, and the log is the one place where raising is the
*correct* behaviour, because a run with no record is not a run this runtime performs.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from runtime.loop import RunLog
from runtime.schemas import ContextRecord, FailureEvent, ModelCallRecord, RunOutput, ToolCallRecord
from runtime.trace import TraceError
from tests.helpers import execute, make_config, text, tool_call, tool_calls

REPO_ROOT = Path(__file__).resolve().parent.parent
LOOP = REPO_ROOT / "runtime" / "loop.py"
REPLAY = REPO_ROOT / "runtime" / "replay.py"


class RecordingLog:
    """A log that implements the boundary and nothing else.

    Deliberately not a `TraceWriter`: the point of the boundary is that a run can be recorded
    somewhere the loop has never heard of, and a test that used the real writer would not show it.
    """

    def __init__(self) -> None:
        self.events: list[str] = []
        self.tool_records: list[ToolCallRecord] = []
        self.trace_id = "a-log-with-no-file"
        self.redacting = False

    def enable_redaction(self, redactor: Any) -> None:
        self.redacting = True

    def run_started(self, **kwargs: Any) -> None:
        self.events.append("run_started")

    def context(self, record: ContextRecord) -> None:
        self.events.append("context")

    def model_call(self, record: ModelCallRecord) -> None:
        self.events.append("model_call")

    def tool_call(self, record: ToolCallRecord) -> None:
        self.events.append("tool_call")
        self.tool_records.append(record)

    def failure(self, event: FailureEvent) -> None:
        self.events.append("failure")

    def run_finished(self, output: RunOutput) -> None:
        self.events.append("run_finished")


class LogThatCannotWrite(RecordingLog):
    """A logger that fails on the tool call, the way a full disk would."""

    def tool_call(self, record: ToolCallRecord) -> None:
        raise TraceError("the record could not be written")


# --------------------------------------------------------------- the boundary itself


def test_the_writer_satisfies_the_boundary(tracer: Any) -> None:
    """Structural, and checked rather than assumed.

    `TraceWriter` does not inherit from `RunLog` — it satisfies it. A renamed or removed method
    fails here, at the boundary, rather than at whichever call site happens to reach it first.
    """
    assert isinstance(tracer, RunLog)


def test_the_boundary_declares_the_whole_vocabulary() -> None:
    """A floor, so the AST check below cannot pass by finding an empty protocol."""
    declared = {name for name in dir(RunLog) if not name.startswith("_")}
    assert declared >= {
        "trace_id",
        "enable_redaction",
        "run_started",
        "context",
        "model_call",
        "tool_call",
        "failure",
        "run_finished",
    }, f"the boundary no longer declares the events a run is made of: {sorted(declared)}"


def test_the_boundary_is_satisfiable_by_something_that_is_not_the_writer() -> None:
    """If only one class could ever satisfy it, the protocol would be ceremony."""
    assert isinstance(RecordingLog(), RunLog)


# ----------------------------------------------------------------- what the loop logs


def test_the_loop_records_one_tool_call_per_dispatch_in_order() -> None:
    """Exactly once, in dispatch order. A drop or a duplicate is invisible in the run and fatal
    to the replay, so it has to be asserted where it is caused."""
    log = RecordingLog()
    execute(
        "go",
        config=make_config(),
        tracer=log,
        script=[
            tool_calls(("echo", {"text": "a"}), ("calculator", {"expression": "1 + 1"})),
            text("done"),
        ],
    )

    assert log.events.count("tool_call") == 2
    assert [record.name for record in log.tool_records] == ["echo", "calculator"]
    assert log.events[0] == "run_started"
    assert log.events[-1] == "run_finished"
    assert log.events.count("run_finished") == 1


def test_a_log_that_cannot_write_stops_the_run_before_the_result_is_used() -> None:
    """The fail-safe clause, and the only place the loop raises for something that is not the
    task's fault.

    Both halves matter. The exception is **not caught**, because a run whose record has a hole in
    it is worse than no run — and the model is never asked again, because a result the record does
    not contain is a model acting on something that did not happen.
    """
    log = LogThatCannotWrite()
    with pytest.raises(TraceError, match="could not be written"):
        execute(
            "go",
            config=make_config(),
            tracer=log,
            script=[tool_call("echo", {"text": "a"}), text("done")],
        )

    assert log.events.count("model_call") == 1, (
        "the model was asked again after the log failed — the result reached it unrecorded"
    )
    assert "run_finished" not in log.events


def test_the_tool_call_is_recorded_before_it_is_interpreted() -> None:
    """The causal order, made observable.

    A failing optional tool produces a `failure` event *because* of its record. If the record were
    written after the loop interpreted it, the failure would appear first — and a trace whose
    events are out of causal order is a trace that describes something that did not happen.
    """
    log = RecordingLog()
    execute(
        "go",
        config=make_config(),
        tracer=log,
        script=[tool_call("ghost"), text("done")],
    )

    assert "failure" in log.events, "the run did not fail the way this test needs"
    assert log.events.index("tool_call") < log.events.index("failure"), (
        f"the failure caused by a tool call is recorded before the call: {log.events}"
    )


def test_the_log_is_a_sink_not_a_participant(tracer: Any) -> None:
    """The same task through two different logs produces the same run.

    This is what makes the record evidence rather than a participant: nothing a logger does can
    change an outcome, so a disagreement between a run and its replay is always a fact about the
    code or the trace, never about the logger.
    """
    script = [tool_call("echo", {"text": "a"}), text("done")]
    through_the_writer = execute("go", config=make_config(), tracer=tracer, script=script)
    through_a_stub = execute("go", config=make_config(), tracer=RecordingLog(), script=script)

    assert through_the_writer.canonical() == through_a_stub.canonical()


# --------------------------------------------------------- the loop knows the boundary only


def imported_modules(path: Path) -> set[str]:
    return {
        node.module
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module
    }


def calls_on_the_log(path: Path) -> set[str]:
    """Every `self.tracer.<name>` in a module, read from the AST rather than the text."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Attribute):
            continue
        receiver = node.value.value
        if isinstance(receiver, ast.Name) and receiver.id == "self" and node.value.attr == "tracer":
            found.add(node.attr)
    return found


def test_the_loop_calls_nothing_the_boundary_does_not_declare() -> None:
    """The tripwire for growth: a new event added to the loop without adding it to `RunLog` fails
    here, so the boundary cannot quietly fall behind the code it describes."""
    called = calls_on_the_log(LOOP)
    declared = {name for name in dir(RunLog) if not name.startswith("_")}

    assert called, "no calls on the log were found — the check is reading nothing"
    assert called <= declared, (
        f"the loop calls {sorted(called - declared)} on its log, which `RunLog` does not declare. "
        f"A boundary that does not describe the code is worse than none: it reads as a contract."
    )


def test_the_loop_does_not_import_the_concrete_writer() -> None:
    """The measurable outcome of declaring the boundary.

    The loop is the thing that must stay general; the writer is how this deployment records runs.
    A loop that imports it has an opinion about how runs are recorded, which is the same defect as
    a loop that names a tool.
    """
    assert "runtime.trace" not in imported_modules(LOOP), (
        "runtime/loop.py imports the concrete writer again. Type the log as `RunLog` and let the "
        "composition root supply the implementation."
    )
    # Non-vacuity: something must import it, or the absence above proves nothing.
    assert "runtime.trace" in imported_modules(REPLAY), (
        "nothing imports runtime.trace any more — the check above is vacuous"
    )
