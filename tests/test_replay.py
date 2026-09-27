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
