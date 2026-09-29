"""The run record: append-only, parseable, and sufficient to reconstruct a run."""

from __future__ import annotations

import json

import pytest

from runtime.schemas import TRACE_SCHEMA_VERSION, ModelCallRecord, ToolCallRecord, TraceEvent
from runtime.trace import (
    TraceError,
    TraceWriter,
    list_traces,
    model_call_records,
    new_trace_id,
    read_trace,
    recorded_config,
    recorded_task,
    tool_call_records,
)
from tests.helpers import SPEND, SpendingTool, execute, make_config, text, tool_call


def test_trace_ids_are_unique() -> None:
    assert len({new_trace_id() for _ in range(200)}) == 200


def test_events_are_appended_and_earlier_lines_are_never_rewritten(tmp_path) -> None:
    """Append-only, checked by bytes rather than by intention."""
    with TraceWriter(tmp_path, "t1") as writer:
        writer.emit("first", {"n": 1})
        after_first = writer.path.read_text(encoding="utf-8")
        writer.emit("second", {"n": 2})
        after_second = writer.path.read_text(encoding="utf-8")

    assert after_second.startswith(after_first)
    assert len(after_second) > len(after_first)
    assert writer.event_count == 2


def test_every_line_carries_the_trace_id(tmp_path) -> None:
    with TraceWriter(tmp_path, "t2") as writer:
        writer.emit("a")
        writer.emit("b")
        path = writer.path
    for line in path.read_text(encoding="utf-8").splitlines():
        assert json.loads(line)["trace_id"] == "t2"


def test_every_line_carries_the_schema_version(tmp_path) -> None:
    """On every line, not in a header, so a mixed file cannot pass as a whole one."""
    with TraceWriter(tmp_path, "t2") as writer:
        writer.emit("a")
        writer.emit("b")
        path = writer.path
    for line in path.read_text(encoding="utf-8").splitlines():
        assert json.loads(line)["schema_version"] == TRACE_SCHEMA_VERSION


def test_a_trace_from_an_older_format_is_refused_clearly(tmp_path) -> None:
    """A format change used to surface as a divergence blaming context assembly.

    That is the wrong diagnosis: an old trace is a fact about the file, not a defect in
    the code, and the message has to say which it is.
    """
    path = tmp_path / "old.jsonl"
    path.write_text(
        json.dumps(
            {
                "schema_version": 0,
                "ts": "a",
                "trace_id": "t",
                "event": "run_started",
                "payload": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(TraceError) as caught:
        read_trace(path)
    message = str(caught.value)
    assert "schema version 0" in message
    assert f"version {TRACE_SCHEMA_VERSION}" in message


def test_a_mixed_version_file_is_refused(tmp_path) -> None:
    """Per-line versioning is what makes this detectable at all."""
    path = tmp_path / "mixed.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(TraceEvent(ts="a", trace_id="t", event="one").model_dump()),
                json.dumps(
                    TraceEvent(ts="b", trace_id="t", event="two", schema_version=99).model_dump()
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(TraceError, match="schema version 99"):
        read_trace(path)


def test_a_trace_written_before_versioning_still_reads(tmp_path) -> None:
    """Version 1 is the format as it stood when the field was added, so a line without it
    is version 1 rather than unknown — which is why the field has a default."""
    path = tmp_path / "preversioning.jsonl"
    path.write_text(
        json.dumps({"ts": "a", "trace_id": "t", "event": "one", "payload": {}}) + "\n",
        encoding="utf-8",
    )
    trace = read_trace(path)
    assert trace.events[0].schema_version == TRACE_SCHEMA_VERSION


def test_a_closed_writer_refuses_to_write(tmp_path) -> None:
    writer = TraceWriter(tmp_path, "t3")
    writer.close()
    with pytest.raises(TraceError, match="closed"):
        writer.emit("late")


def test_reading_back_a_run_reconstructs_it(tmp_path, tracer) -> None:
    config = make_config()
    execute(
        "what is 1 + 1?",
        config=config,
        tracer=tracer,
        script=[tool_call("calculator", {"expression": "1 + 1"}), text("2")],
    )

    trace = read_trace(tracer.path)
    assert trace.trace_id == tracer.trace_id
    assert recorded_task(trace) == "what is 1 + 1?"
    assert recorded_config(trace).name == config.name
    assert len(model_call_records(trace)) == 2
    assert len(tool_call_records(trace)) == 1
    assert trace.first("run_started") is not None
    assert trace.first("run_finished") is not None


def test_the_recorded_configuration_is_the_one_that_ran(tmp_path, tracer) -> None:
    config = make_config(name="snapshot")
    execute("go", config=config, tracer=tracer, script=[text("done")])
    assert recorded_config(read_trace(tracer.path)).name == "snapshot"


def test_recorded_model_responses_are_complete_enough_to_replay(tmp_path, tracer) -> None:
    config = make_config()
    execute(
        "go",
        config=config,
        tracer=tracer,
        script=[tool_call("calculator", {"expression": "2 * 3"}), text("6")],
    )
    records = model_call_records(read_trace(tracer.path))
    assert isinstance(records[0], ModelCallRecord)
    assert records[0].response.tool_calls[0].name == "calculator"
    assert records[0].prompt_hash
    assert records[1].response.text == "6"


def test_a_trailing_partial_line_is_tolerated(tmp_path) -> None:
    """A process killed mid-write leaves one. Refusing to read the trace then would
    make the record least useful exactly when it matters most."""
    with TraceWriter(tmp_path, "t4") as writer:
        writer.emit("a")
        writer.emit("b")
        path = writer.path
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"ts": "x", "trace_id": "t4", "event": "run_fin')

    trace = read_trace(path)
    assert [event.event for event in trace.events] == ["a", "b"]


def test_a_malformed_line_in_the_middle_is_an_error(tmp_path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(TraceEvent(ts="a", trace_id="t", event="one").model_dump()),
                "not json at all",
                json.dumps(TraceEvent(ts="b", trace_id="t", event="two").model_dump()),
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(TraceError, match="not JSON"):
        read_trace(path)


def test_mixed_trace_ids_are_refused(tmp_path) -> None:
    path = tmp_path / "mixed.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(TraceEvent(ts="a", trace_id="one", event="x").model_dump()),
                json.dumps(TraceEvent(ts="b", trace_id="two", event="y").model_dump()),
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(TraceError, match="trace_id"):
        read_trace(path)


def test_an_empty_trace_is_an_error(tmp_path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(TraceError, match="no events"):
        read_trace(path)


def test_a_missing_trace_is_an_error(tmp_path) -> None:
    with pytest.raises(TraceError, match="no such trace"):
        read_trace(tmp_path / "absent.jsonl")


def test_listing_traces_ignores_other_files(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")
    with TraceWriter(tmp_path, "t5"):
        pass
    assert [path.name for path in list_traces(tmp_path)] == ["t5.jsonl"]


def test_a_run_emits_exactly_one_run_finished(tmp_path, tracer) -> None:
    """Every exit path finishes once. Two would mean two accounts of one run."""
    config = make_config()
    execute("go", config=config, tracer=tracer, script=[tool_call("ghost"), text("done")])
    assert len(read_trace(tracer.path).of("run_finished")) == 1


def test_tool_records_round_trip(tmp_path, tracer) -> None:
    config = make_config()
    execute(
        "go",
        config=config,
        tracer=tracer,
        script=[tool_call("calculator", {"expression": "1 + 1"}), text("2")],
    )
    record = tool_call_records(read_trace(tracer.path))[0]
    assert isinstance(record, ToolCallRecord)
    assert record.name == "calculator"
    assert record.attempt_outcomes


def test_a_tool_records_spend_round_trips(tmp_path, tracer) -> None:
    """Non-zero on purpose: zero is the field's default, so a trace that dropped it entirely
    would still round-trip to zero and look right.

    This is the hop the whole mechanism rests on — the record is where replay reads the charge
    from, because on replay the tool never runs
    ([decisions/0031](../docs/decisions/0031-the-spend-is-in-the-record.md)).
    """
    config = make_config(tools=["spending"])
    execute(
        "go",
        config=config,
        tracer=tracer,
        tools=[SpendingTool()],
        script=[tool_call("spending"), text("done")],
    )
    record = tool_call_records(read_trace(tracer.path))[0]
    assert record.spend == SPEND
    assert not record.spend.is_zero
