"""Replay: the trace alone reconstructs the run, and replay changes nothing.

Two claims, both load-bearing:

1. **Faithful.** A replayed run's canonical projection is byte-identical to the
   original's, prompt hashes included.
2. **Safe.** Replay never executes a tool, so replaying a trace of a mutating run
   cannot mutate anything a second time.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from providers.replay import ReplayDivergence
from runtime.replay import replay
from runtime.schemas import RunOutput
from runtime.trace import read_trace
from tests.helpers import execute, make_config, text, tool_call, tool_calls


def original_output(trace_path: Path) -> RunOutput:
    finished = read_trace(trace_path).first("run_finished")
    assert finished is not None
    return RunOutput.model_validate(finished.payload)


def test_replay_reproduces_the_run_exactly(tmp_path, tracer) -> None:
    config = make_config()
    execute(
        "what is 1 + 1?",
        config=config,
        tracer=tracer,
        script=[tool_call("calculator", {"expression": "1 + 1"}), text("It is 2.")],
    )

    replayed = replay(tracer.path, trace_dir=tmp_path / "replay")
    assert replayed.canonical() == original_output(tracer.path).canonical()


def test_replay_rebuilds_the_same_prompts(tmp_path, tracer) -> None:
    """The strong form: identical prompt hashes mean context assembly is deterministic."""
    config = make_config()
    execute(
        "go",
        config=config,
        tracer=tracer,
        script=[tool_call("calculator", {"expression": "2 * 3"}), text("6")],
    )
    replayed = replay(tracer.path, trace_dir=tmp_path / "replay")
    assert replayed.prompt_hashes == original_output(tracer.path).prompt_hashes


def test_replay_of_a_retried_call_keeps_the_recorded_attempts(tmp_path, tracer) -> None:
    from tools.scripted import ScriptedTool

    config = make_config()
    execute(
        "go",
        config=config,
        tracer=tracer,
        tools=[ScriptedTool(name="calculator", behaviour="error_then_ok")],
        script=[tool_call("calculator", {"expression": "1 + 1"}), text("2")],
    )
    original = original_output(tracer.path)
    replayed = replay(tracer.path, trace_dir=tmp_path / "replay")

    assert original.tool_calls[0].attempts == 2
    assert replayed.tool_calls[0].attempts == 2
    assert replayed.tool_calls[0].attempt_outcomes == original.tool_calls[0].attempt_outcomes


def test_replay_never_executes_a_tool(tmp_path, tracer) -> None:
    """The reason replay is worth having: it cannot repeat a side effect."""
    notes = tmp_path / "notes"
    config = make_config(tools=["write_note"], name="stateful")
    execute(
        "save a note",
        config=config,
        tracer=tracer,
        confirmation_token="tok",
        notes_root=notes,
        script=[tool_call("write_note", {"filename": "a.txt", "text": "hello"}), text("saved")],
    )

    written = notes / "a.txt"
    assert written.is_file()
    written.unlink()

    replayed = replay(tracer.path, trace_dir=tmp_path / "replay")
    assert replayed.status == "ok"
    assert not written.exists(), "replay executed the side effect again"


def test_a_tampered_prompt_hash_is_caught(tmp_path, tracer) -> None:
    """Non-vacuous: the check is only worth having if it can fail."""
    config = make_config()
    execute("go", config=config, tracer=tracer, script=[text("done")])

    lines = tracer.path.read_text(encoding="utf-8").splitlines()
    tampered: list[str] = []
    for line in lines:
        payload = json.loads(line)
        if payload["event"] == "model_call":
            payload["payload"]["prompt_hash"] = "0" * 64
        tampered.append(json.dumps(payload))

    forged = tmp_path / "forged.jsonl"
    forged.write_text("\n".join(tampered) + "\n", encoding="utf-8")

    with pytest.raises(ReplayDivergence, match="does not match the recorded one"):
        replay(forged, trace_dir=tmp_path / "replay")


def test_a_run_that_diverges_is_caught(tmp_path, tracer) -> None:
    """A replay that takes a different path must fail loudly, not quietly succeed."""
    config = make_config()
    execute(
        "go",
        config=config,
        tracer=tracer,
        script=[tool_call("calculator", {"expression": "1 + 1"}), text("2")],
    )

    lines = tracer.path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if json.loads(line)["event"] != "tool_call"]
    forged = tmp_path / "no-tool-call.jsonl"
    forged.write_text("\n".join(kept) + "\n", encoding="utf-8")

    with pytest.raises(ReplayDivergence):
        replay(forged, trace_dir=tmp_path / "replay")


def test_replay_of_a_multi_tool_run_is_exact(tmp_path, tracer) -> None:
    config = make_config()
    execute(
        "go",
        config=config,
        tracer=tracer,
        script=[
            tool_calls(("calculator", {"expression": "1 + 1"}), ("echo", {"text": "hi"})),
            text("done"),
        ],
    )
    replayed = replay(tracer.path, trace_dir=tmp_path / "replay")
    assert replayed.canonical() == original_output(tracer.path).canonical()


# --------------------------------------------------- the repair pass, and key order


def test_replay_reproduces_a_run_that_used_the_repair_pass(tmp_path, tracer) -> None:
    """A run whose structured answer needed repair must still replay exactly.

    This is where the schema's **key order** bit, and it made every repair-using run
    unreplayable.

    A trace line is written with `sort_keys=True`, so a configuration read back *out of a
    trace* has alphabetical key order — while the same configuration parsed from YAML has
    insertion order. The repair note serialised the schema in whatever order it arrived in, so
    the original run and its replay built different prompts and diverged.

    It survived because nothing replayed the repair path: `make_config()` is text-output, so
    structured output never engaged. The first real domain's first live run hit the repair pass,
    and its replay refused the trace.
    """
    schema = {
        "type": "object",
        "required": ["zeta", "alpha"],
        # Deliberately not alphabetical, so insertion order differs from sorted order.
        "properties": {"zeta": {"type": "string"}, "alpha": {"type": "string"}},
    }
    config = make_config(output={"format": "json", "schema": schema})
    execute(
        "answer",
        config=config,
        tracer=tracer,
        # The first answer is unusable, so the repair note joins the next prompt.
        script=[text('{"zeta": 1}'), text('{"zeta": "ok", "alpha": "ok"}')],
    )
    replayed = replay(tracer.path, trace_dir=tmp_path / "replay")
    assert replayed.canonical() == original_output(tracer.path).canonical()


def test_the_repair_pass_actually_ran_in_that_test(tmp_path, tracer) -> None:
    """A guard on the guard: if the repair stopped running, the test above proves nothing."""
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    config = make_config(output={"format": "json", "schema": schema})
    execute(
        "answer",
        config=config,
        tracer=tracer,
        script=[text('{"a": 1}'), text('{"a": "ok"}')],
    )
    assert original_output(tracer.path).steps == 2, "no repair pass ran"


def test_the_repair_note_does_not_depend_on_key_order() -> None:
    """The same schema in two orders must produce the same note.

    A direct test of the fix, so a future edit to `_repair_note` fails here rather than
    somewhere a replay happens to notice.
    """
    from runtime.loop import _repair_note

    forward = make_config(
        output={"format": "json", "schema": {"b": {"type": "string"}, "a": {"type": "string"}}}
    )
    backward = make_config(
        output={"format": "json", "schema": {"a": {"type": "string"}, "b": {"type": "string"}}}
    )
    assert _repair_note("something went wrong", forward) == _repair_note(
        "something went wrong", backward
    )


def test_the_repair_note_still_carries_the_schema() -> None:
    """Order-independent, not schema-free — the model needs to see the shape."""
    from runtime.loop import _repair_note

    config = make_config(output={"format": "json", "schema": {"a": {"type": "string"}}})
    note = _repair_note("e", config)
    assert '"a"' in note
    assert "valid JSON" in note


def test_every_json_dump_in_the_runtime_sorts_its_keys() -> None:
    """Order-dependent serialisation is a replay defect, not a style preference.

    The rule this enforces, and the reason it is a test rather than a habit: **anything
    derived from a configuration and placed in a prompt must serialise the same way whichever
    door the configuration came through.** A trace line is written with sorted keys, so a
    configuration read back out of a trace has alphabetical order, while the same configuration
    parsed from YAML has insertion order. Serialising in arrival order gives two different
    strings for the same value, and the run and its replay hash differently.

    A blanket rule rather than a targeted one, because the failure is invisible until something
    replays — and there are only two call sites, both of which want sorting anyway.
    """
    offenders: list[str] = []
    for path in sorted((Path(__file__).resolve().parent.parent / "runtime").glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "json.dumps(" in line and "sort_keys=True" not in line:
                offenders.append(f"{path.name}:{number}")
    assert not offenders, (
        f"json.dumps without sort_keys in the runtime: {offenders}. If the value reaches a "
        f"prompt, the run will not replay."
    )


def test_the_sort_keys_check_is_not_vacuous() -> None:
    """It must actually find the call sites, or it proves nothing."""
    runtime = Path(__file__).resolve().parent.parent / "runtime"
    calls = sum(
        line.count("json.dumps(")
        for path in runtime.glob("*.py")
        for line in path.read_text(encoding="utf-8").splitlines()
    )
    assert calls >= 2, f"only {calls} json.dumps call(s) found — the check has gone vacuous"
