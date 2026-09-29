"""The loop: orchestration behaviour that is not a taxonomy row.

The taxonomy rows are forced in ``test_taxonomy.py``. What is left here is the shape of
a run — what the transcript looks like, what the trace contains, and the ways a run can
end that are not failures.
"""

from __future__ import annotations

from providers.stub import StubProvider
from runtime.loop import MAX_TOOL_CALLS_PER_STEP
from runtime.replay import replay
from runtime.schemas import RunOutput
from runtime.status import FailureClass, RunStatus
from runtime.trace import read_trace
from tests.helpers import execute, make_config, text, tool_call, tool_calls


def test_tool_output_reaches_the_transcript_inside_the_envelope(tracer) -> None:
    """Untrusted content arrives wrapped, and the wrapper is in-band with the data."""
    config = make_config()
    provider = StubProvider(script=[tool_call("calculator", {"expression": "2 + 2"}), text("4")])
    execute("go", config=config, tracer=tracer, provider=provider)

    assert provider.calls_made == 2
    second_prompt = provider.calls[1]
    assert second_prompt[0]["role"] == "system"
    assert second_prompt[1] == {"role": "user", "content": "go"}

    envelopes = [
        message
        for message in second_prompt
        if message["role"] == "user" and "<<<UNTRUSTED_TOOL_OUTPUT" in message["content"]
    ]
    assert len(envelopes) == 1
    assert "tool=calculator" in envelopes[0]["content"]
    assert "4" in envelopes[0]["content"]


def test_the_tool_call_is_rendered_in_band_for_the_next_turn(tracer) -> None:
    config = make_config()
    provider = StubProvider(script=[tool_call("echo", {"text": "hi"}), text("done")])
    execute("go", config=config, tracer=tracer, provider=provider)
    assistant_turns = [m for m in provider.calls[1] if m["role"] == "assistant"]
    assert assistant_turns
    assert "[called echo(" in assistant_turns[0]["content"]


def test_a_tool_that_raises_unexpectedly_does_not_crash_the_run(tracer) -> None:
    from pydantic import BaseModel, ConfigDict

    from tools.registry import Tool

    class EmptyArgs(BaseModel):
        model_config = ConfigDict(extra="forbid")

    class Exploding(Tool):
        name = "exploding"
        description = "Raises something undeclared."
        args_model = EmptyArgs

        def invoke(self, args: BaseModel) -> str:
            raise RuntimeError("boom")

    config = make_config()
    output = execute(
        "go",
        config=config,
        tracer=tracer,
        tools=[Exploding()],
        script=[tool_call("exploding"), text("carried on")],
    )
    assert output.status is RunStatus.DEGRADED
    assert output.output == "carried on"


def test_an_empty_model_turn_is_reported_not_silently_accepted(tracer) -> None:
    config = make_config()
    output = execute("go", config=config, tracer=tracer, script=[{"text": "   "}])
    assert output.status is RunStatus.DEGRADED
    assert output.reason == "empty_model_turn"
    assert output.output == ""


def test_a_single_answer_run_takes_one_step_and_one_call(tracer) -> None:
    config = make_config()
    output = execute("go", config=config, tracer=tracer, script=[text("done")])
    assert (output.steps, output.model_calls) == (1, 1)


def test_a_run_with_no_tools_still_works(tracer) -> None:
    config = make_config(tools=[])
    output = execute("go", config=config, tracer=tracer, script=[text("done")])
    assert output.status is RunStatus.OK
    assert output.tool_calls == []


def test_every_model_call_is_recorded_with_a_prompt_hash(tracer) -> None:
    config = make_config()
    output = execute(
        "go",
        config=config,
        tracer=tracer,
        script=[tool_call("calculator", {"expression": "1 + 1"}), text("2")],
    )
    trace = read_trace(tracer.path)
    calls = trace.of("model_call")
    assert len(calls) == output.model_calls
    assert all(event.payload["prompt_hash"] for event in calls)
    assert [event.payload["prompt_hash"] for event in calls] == output.prompt_hashes


def test_every_step_records_context_evidence(tracer) -> None:
    config = make_config()
    execute("go", config=config, tracer=tracer, script=[text("done")])
    contexts = read_trace(tracer.path).of("context")
    assert len(contexts) == 1
    assert contexts[0].payload["system_prompt_present"] is True
    assert contexts[0].payload["task_present"] is True


def test_a_run_reports_its_own_identity(tracer) -> None:
    config = make_config(name="identity")
    output = execute("go", config=config, tracer=tracer, script=[text("done")])
    assert output.config_name == "identity"
    assert output.trace_id == tracer.trace_id
    assert output.scorer == "deterministic"


def test_the_status_is_derived_from_the_most_severe_claim(tracer) -> None:
    """One degraded tool plus a guardrail trip is a refusal, not a degradation."""
    from tools.scripted import ScriptedTool

    config = make_config()
    output = execute(
        "go",
        config=config,
        tracer=tracer,
        tools=[
            ScriptedTool(name="calculator", behaviour="timeout"),
            ScriptedTool(name="echo", behaviour="inject"),
        ],
        script=[
            tool_calls(("calculator", {"expression": "1"}), ("echo", {"text": "x"})),
            text("done"),
        ],
    )
    assert output.status is RunStatus.REFUSED
    assert len(output.failures) >= 2
    # Nothing is hidden: both claims survive in the record.
    assert any(event.status is RunStatus.DEGRADED for event in output.failures)


# ------------------------------------------------- how many calls one step may make


def original_output(trace_path) -> RunOutput:
    """The run as the trace recorded it."""
    finished = read_trace(trace_path).first("run_finished")
    assert finished is not None
    return RunOutput.model_validate(finished.payload)


def test_a_step_may_not_dispatch_an_unbounded_number_of_tool_calls(tracer) -> None:
    """`max_steps` bounds steps. Nothing bounded the calls inside one.

    A single model response may carry an arbitrary number of tool calls, and the loop
    dispatched every one of them before consulting any budget — so one step could run for
    `calls x timeout_s` against a wall-clock bound it was unable to interrupt. The spec says
    no unbounded path may exist; this was one, in a place nobody had looked.
    """
    config = make_config(budget={"max_steps": 6})
    asked = MAX_TOOL_CALLS_PER_STEP * 3
    execute(
        "go",
        config=config,
        tracer=tracer,
        script=[tool_calls(*[("echo", {"text": f"c{n}"}) for n in range(asked)]), text("done")],
    )
    assert len(original_output(tracer.path).tool_calls) == MAX_TOOL_CALLS_PER_STEP


def test_the_dropped_calls_are_recorded_not_silent(tracer) -> None:
    """The class is `budget_exhausted` — an existing row, with the limit named.

    Not a new taxonomy row: this is a budget, and `budget_exhausted:{limit}` already means
    "a declared bound stopped something". Reusing it keeps the taxonomy closed.
    """
    config = make_config(budget={"max_steps": 6})
    execute(
        "go",
        config=config,
        tracer=tracer,
        script=[
            tool_calls(*[("echo", {"text": f"c{n}"}) for n in range(MAX_TOOL_CALLS_PER_STEP + 4)]),
            text("done"),
        ],
    )
    output = original_output(tracer.path)
    assert FailureClass.BUDGET_EXHAUSTED in {FailureClass(c) for c in output.failure_classes}
    assert output.status == RunStatus.DEGRADED, "the run can still continue"
    detail = " ".join(f.detail or "" for f in output.failures)
    assert str(MAX_TOOL_CALLS_PER_STEP) in detail


def test_the_model_is_told_that_calls_were_dropped(tracer) -> None:
    """Recorded is not enough — the model has to know, or it cannot ask again.

    A turn that was silently half-answered is the failure this design keeps refusing to
    have, which is why the loss is signalled in-band rather than only in the trace.
    """
    config = make_config(budget={"max_steps": 6})
    provider = StubProvider(
        script=[
            tool_calls(*[("echo", {"text": f"c{n}"}) for n in range(MAX_TOOL_CALLS_PER_STEP + 3)]),
            text("done"),
        ]
    )
    execute("go", config=config, tracer=tracer, provider=provider)

    second_prompt = provider.calls[1]
    assert "were not acted on" in str(second_prompt), "the model was not told"


def test_a_step_at_the_limit_is_untouched(tracer) -> None:
    """The cap must not fire on an ordinary turn — a guard on the guard."""
    config = make_config(budget={"max_steps": 6})
    execute(
        "go",
        config=config,
        tracer=tracer,
        script=[
            tool_calls(*[("echo", {"text": f"c{n}"}) for n in range(MAX_TOOL_CALLS_PER_STEP)]),
            text("done"),
        ],
    )
    output = original_output(tracer.path)
    assert len(output.tool_calls) == MAX_TOOL_CALLS_PER_STEP
    assert output.status == RunStatus.OK, "exactly at the limit is not over it"
    assert output.failure_classes == []


def test_the_cap_is_deterministic_so_a_capped_run_still_replays(tmp_path, tracer) -> None:
    """A *count*, not a clock check, so replay stays exact.

    `runtime/replay.py` says a wall-clock-bounded run only replays under a deterministic
    clock. A budget check inside the dispatch loop would have made the number of dispatches
    depend on the clock and widened that requirement to every run. This does not.
    """
    config = make_config(budget={"max_steps": 6})
    execute(
        "go",
        config=config,
        tracer=tracer,
        script=[
            tool_calls(*[("echo", {"text": f"c{n}"}) for n in range(MAX_TOOL_CALLS_PER_STEP + 5)]),
            text("done"),
        ],
    )
    replayed = replay(tracer.path, trace_dir=tmp_path / "replay")
    assert replayed.canonical() == original_output(tracer.path).canonical()


# --------------------------------------------- one turn, several tool calls


def test_tool_calls_in_one_turn_are_run_one_at_a_time(tracer) -> None:
    """Sequential, and that is load-bearing rather than incidental.

    `runtime/budget.py` gives a child a share of what is *left* and relies on each child's spend
    being charged back before the next one starts. Its own docstring says a *reservation* would
    be needed for concurrent children "and there are none", and decisions/0030 repeats it.
    **Nothing asserted it.** If dispatch were ever parallelised for speed, two spawns in one turn
    would each allocate from the same untouched remainder and the declared bound would multiply —
    decisions/0015's defect arriving by the back door.

    The sleep is what makes this a measurement rather than a restatement of a `for` loop:
    without it, a concurrent implementation could still finish one call before the next one's
    future is submitted, and the test would pass on a runtime that no longer had the property.
    """
    import time

    from pydantic import BaseModel, ConfigDict

    from tools.registry import Tool

    order: list[str] = []

    class LabelArgs(BaseModel):
        model_config = ConfigDict(extra="forbid")
        label: str

    class Recording(Tool):
        name = "recording"
        description = "Notes when it starts and stops."
        args_model = LabelArgs

        def invoke(self, args: BaseModel) -> str:
            assert isinstance(args, LabelArgs)
            order.append(f"start:{args.label}")
            time.sleep(0.05)
            order.append(f"stop:{args.label}")
            return args.label

    execute(
        "go",
        config=make_config(tools=["recording"]),
        tracer=tracer,
        tools=[Recording()],
        script=[
            tool_calls(("recording", {"label": "a"}), ("recording", {"label": "b"})),
            text("done"),
        ],
    )

    assert order == ["start:a", "stop:a", "start:b", "stop:b"], (
        "two tool calls in one turn overlapped. If dispatch was deliberately made concurrent, "
        "`Budget.allocate` needs a reservation first — see runtime/budget.py and "
        "docs/decisions/0030, and reword `spawn_agent`'s description."
    )
