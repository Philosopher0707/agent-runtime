"""The loop: orchestration behaviour that is not a taxonomy row.

The taxonomy rows are forced in ``test_taxonomy.py``. What is left here is the shape of
a run — what the transcript looks like, what the trace contains, and the ways a run can
end that are not failures.
"""

from __future__ import annotations

from providers.stub import StubProvider
from runtime.status import RunStatus
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
