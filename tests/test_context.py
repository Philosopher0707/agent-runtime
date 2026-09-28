"""Context assembly: the invariants that must hold, and the pressure that tests them.

The load-bearing claim is "the system prompt and the task are never dropped". A claim
like that is worth nothing unless something tries to break it, so the tests below push
the assembler past both thresholds and check the pair is still there.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from context.assembler import ContextAssembler, ContextUnfit, summarise
from runtime.config import ContextConfig
from runtime.schemas import ToolCallRequest, ToolDescriptor


def make_assembler(
    *,
    system: str = "S" * 40,
    task: str = "T" * 40,
    tool_schemas: Sequence[ToolDescriptor] = (),
    **overrides,
) -> ContextAssembler:
    config = ContextConfig(
        **{"max_prompt_tokens": 10_000, "summarise_above_tokens": 9_000, **overrides}
    )
    return ContextAssembler(
        system_prompt=system, task=task, config=config, tool_schemas=tool_schemas
    )


def schema(name: str = "tool") -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description="Does a thing, at some length, so the schema is not trivially small.",
        parameters={"type": "object", "properties": {"a": {"type": "string"}}},
        side_effect=False,
        idempotent=True,
        optional=True,
    )


def test_the_system_prompt_and_task_are_always_present() -> None:
    assembler = make_assembler(
        system="S" * 10,
        task="T" * 10,
        summarise_above_tokens=250,
        summary_chars=40,
    )
    for _ in range(3):
        assembler.add_tool_result(name="tool", envelope="R" * 400)

    assembled = assembler.build(step=1)
    assert assembled.messages[0] == {"role": "system", "content": "S" * 10}
    assert assembled.messages[1] == {"role": "user", "content": "T" * 10}
    assert assembled.record.system_prompt_present is True
    assert assembled.record.task_present is True


def test_tool_results_are_summarised_oldest_first() -> None:
    assembler = make_assembler(
        system="S" * 10,
        task="T" * 10,
        summarise_above_tokens=250,
        summary_chars=40,
    )
    for index in range(3):
        assembler.add_tool_result(name=f"tool{index}", envelope="R" * 400)

    assembled = assembler.build(step=1)
    tool_messages = assembled.messages[2:]
    assert assembled.record.summarised_results == 1
    assert tool_messages[0]["content"].startswith("[summarised]")
    assert not tool_messages[1]["content"].startswith("[summarised]")
    assert not tool_messages[2]["content"].startswith("[summarised]")


def test_summarisation_continues_until_the_soft_threshold_is_met() -> None:
    assembler = make_assembler(
        system="S" * 10,
        task="T" * 10,
        summarise_above_tokens=120,
        summary_chars=40,
    )
    for index in range(3):
        assembler.add_tool_result(name=f"tool{index}", envelope="R" * 400)

    assembled = assembler.build(step=1)
    assert assembled.record.summarised_results == 3


def test_dropping_only_happens_under_the_hard_ceiling_and_is_counted() -> None:
    """Dropping removes a whole group — a request and the results it produced.

    The request turn is part of the fixture now, and that is the point: a transcript of tool
    results with no requests is what the old drop produced, and it is not a transcript any
    model can make sense of.
    """
    assembler = make_assembler(
        system="S" * 10,
        task="T" * 10,
        max_prompt_tokens=10,
        summarise_above_tokens=250,
        summary_chars=40,
    )
    # Two requests, each answered. Only the most recent group may be kept.
    assembler.add_assistant("first question", tool_calls=[])
    for index in range(3):
        assembler.add_tool_result(name=f"tool{index}", envelope="R" * 400)
    assembler.add_assistant("second question", tool_calls=[])

    assembled = assembler.build(step=1)
    assert assembled.record.dropped_messages >= 1, "the ceiling forced a drop"
    assert assembled.messages[0]["content"] == "S" * 10
    assert assembled.messages[1]["content"] == "T" * 10
    # The most recent request survives; the transcript never ends up empty.
    assert any("second question" in str(m.get("content")) for m in assembled.messages)


def test_the_transcript_never_keeps_a_result_whose_request_was_dropped() -> None:
    """The invariant pairing buys: after the protected pair, no leading tool result.

    Before this, dropping single turns left the model with sixteen answers and none of its
    own questions — the prompt began with a result, and the model could not tell what it had
    asked.
    """
    assembler = make_assembler(
        system="S" * 10, task="T" * 10, max_prompt_tokens=200, summarise_above_tokens=100
    )
    for _ in range(3):
        assembler.add_assistant("a question", tool_calls=[])
        assembler.add_tool_result(name="tool", envelope="R" * 200)

    assembled = assembler.build(step=1)
    turns = assembled.messages[2:]
    assert turns, "the most recent group is kept"
    assert "UNTRUSTED_TOOL_OUTPUT" not in str(turns[0].get("content")), (
        "the transcript begins with a tool result, so its request was dropped"
    )


def test_a_group_too_large_to_fit_raises_rather_than_sending_half_of_it() -> None:
    """Measured, not guessed: pairing alone emptied the transcript.

    With one 65,000-token group, dropping it left nothing at all — which is worse for the
    model than a transcript it cannot parse. So the last group is kept, and if even that
    cannot fit, the assembler refuses and the run reports `context_overflow`.
    """
    assembler = make_assembler(
        system="S" * 10, task="T" * 10, max_prompt_tokens=50, summarise_above_tokens=20
    )
    assembler.add_assistant("one enormous question", tool_calls=[])
    assembler.add_tool_result(name="tool", envelope="R" * 4_000)

    with pytest.raises(ContextUnfit):
        assembler.build(step=1)


def test_a_protected_pair_that_cannot_fit_raises_rather_than_lying() -> None:
    assembler = make_assembler(system="S" * 400, task="T" * 40, max_prompt_tokens=10)
    with pytest.raises(ContextUnfit) as caught:
        assembler.build(step=1)
    assert caught.value.max_prompt_tokens == 10
    assert caught.value.estimated_tokens > 10


def test_a_runtime_note_is_not_wrapped_as_untrusted() -> None:
    """The runtime is trusted; only tool output is not."""
    assembler = make_assembler()
    assembler.add_runtime_note("Your last answer was rejected.")
    assembled = assembler.build(step=1)
    assert assembled.messages[-1]["content"] == "Your last answer was rejected."


def test_assistant_tool_calls_are_rendered_in_band() -> None:
    from runtime.schemas import ToolCallRequest

    assembler = make_assembler()
    assembler.add_assistant(
        "let me check",
        tool_calls=[ToolCallRequest(name="calculator", arguments={"expression": "1+1"})],
    )
    assembled = assembler.build(step=1)
    content = assembled.messages[2]["content"]
    assert "let me check" in content
    assert "[called calculator(" in content


def test_summarise_is_deterministic_and_single_line() -> None:
    text = "line one\nline two\n   line three"
    first = summarise(text, max_chars=200)
    assert first == summarise(text, max_chars=200)
    assert "\n" not in first
    assert first.startswith("[summarised]")


def test_summarise_marks_truncation() -> None:
    assert summarise("x" * 500, max_chars=20).endswith("...")


def test_estimation_uses_the_configured_ratio() -> None:
    coarse = make_assembler(chars_per_token=2.0)
    fine = make_assembler(chars_per_token=8.0)
    for assembler in (coarse, fine):
        assembler.add_tool_result(name="tool", envelope="R" * 400)
    assert coarse.build(step=1).record.estimated_tokens > fine.build(step=1).record.estimated_tokens


# --------------------------------------------- the fixed per-request overhead


def test_tool_schemas_are_counted() -> None:
    """They are sent as the request's `tools` field, so the provider bills them.

    Omitting them under-counted the fixed overhead by about 600 tokens against a real
    endpoint, which meant both thresholds operated on a number roughly ten times too small.
    """
    without = make_assembler().build(step=1).record.estimated_tokens
    with_schemas = make_assembler(tool_schemas=[schema()]).build(step=1).record.estimated_tokens
    assert with_schemas > without


def test_more_schemas_cost_more() -> None:
    one = make_assembler(tool_schemas=[schema("a")]).build(step=1).record.estimated_tokens
    three = (
        make_assembler(tool_schemas=[schema(n) for n in "abc"])
        .build(step=1)
        .record.estimated_tokens
    )
    assert three > one


def test_schemas_use_their_own_ratio_not_the_prose_one() -> None:
    """JSON tokenises at roughly half the characters-per-token of prose."""
    coarse = make_assembler(schema_chars_per_token=8.0, tool_schemas=[schema()])
    fine = make_assembler(schema_chars_per_token=2.0, tool_schemas=[schema()])
    assert fine.build(step=1).record.estimated_tokens > coarse.build(step=1).record.estimated_tokens


def test_no_tools_means_no_overhead() -> None:
    assert (
        make_assembler(tool_schemas=[]).build(step=1).record.estimated_tokens
        == make_assembler().build(step=1).record.estimated_tokens
    )


def test_the_soft_threshold_measures_the_transcript_not_the_overhead() -> None:
    """Summarising a tiny tool result to make room for a schema that never changes would
    be absurd — so the soft threshold ignores the fixed part.

    The threshold is set *below the overhead but above the transcript*: if the soft
    threshold measured the total, this run would summarise; measuring the transcript, it
    does not.
    """
    schemas = [schema(n) for n in "abc"]
    overhead = make_assembler(tool_schemas=schemas).build(step=1).record.estimated_tokens

    assembler = make_assembler(
        tool_schemas=schemas,
        summarise_above_tokens=overhead // 2,  # below the overhead, above a short result
        summary_chars=40,
    )
    assembler.add_tool_result(name="tool", envelope="short result")
    built = assembler.build(step=1)

    assert built.record.summarised_results == 0, "the fixed overhead triggered summarisation"
    assert built.record.estimated_tokens > overhead // 2, "the overhead is still in the total"


def test_the_hard_ceiling_measures_the_whole_request() -> None:
    """The ceiling is about what the model has to fit, so it includes the overhead."""
    overhead = make_assembler(tool_schemas=[schema(n) for n in "abc"]).build(step=1)
    with pytest.raises(ContextUnfit):
        make_assembler(
            tool_schemas=[schema(n) for n in "abc"],
            max_prompt_tokens=overhead.record.estimated_tokens - 1,
        ).build(step=1)


def test_a_ceiling_below_the_overhead_refuses_rather_than_lying() -> None:
    with pytest.raises(ContextUnfit):
        make_assembler(tool_schemas=[schema(n) for n in "abc"], max_prompt_tokens=10).build(step=1)


# ------------------------------------------- what the model asked for is bounded too


def call(name: str = "tool", size: int = 50) -> ToolCallRequest:
    return ToolCallRequest(name=name, arguments={"text": "x" * size})


def test_a_turns_rendered_calls_are_bounded() -> None:
    """A tool result is bounded by the envelope. The *request* was bounded by nothing.

    The assistant turn carries every call's arguments in full, so sixteen calls with
    8,000-character arguments added ~65,000 tokens in one step — which summarisation cannot
    touch, because it only rewrites tool results, and which the hard ceiling could only answer
    by dropping the request away from its own results.
    """
    assembler = make_assembler(max_call_chars=200)
    assembler.add_assistant("", tool_calls=[call(size=5_000)])

    rendered = assembler.build(step=1).messages[2]["content"]
    assert len(rendered) < 400, "the turn was not bounded"
    assert "omitted by the runtime" in rendered, "the loss was silent"


def test_the_bound_is_on_the_turn_not_on_each_call() -> None:
    """Sixteen bounded calls still add up, so the budget is the turn's."""
    assembler = make_assembler(max_call_chars=300)
    assembler.add_assistant("", tool_calls=[call(size=500) for _ in range(16)])

    rendered = assembler.build(step=1).messages[2]["content"]
    assert len(rendered) < 500, f"sixteen calls produced {len(rendered)} characters"


def test_an_ordinary_turn_is_untouched() -> None:
    """A guard on the bound: it must not fire on the normal case."""
    assembler = make_assembler(max_call_chars=4_000)
    assembler.add_assistant("thinking", tool_calls=[call(size=20)])

    rendered = assembler.build(step=1).messages[2]["content"]
    assert "omitted" not in rendered
    assert "thinking" in rendered
    assert "tool(" in rendered


def test_bounding_the_request_stops_the_ceiling_being_forced() -> None:
    """The point of the fix, stated as a test.

    Before it, a step of large calls pushed the transcript over the hard ceiling, and the only
    answer was to drop the request — which is what made the transcript incoherent. With the
    request bounded, the same calls fit.
    """
    big_calls = [call(size=20_000) for _ in range(8)]

    unbounded = make_assembler(
        max_prompt_tokens=2_000, summarise_above_tokens=1_500, max_call_chars=100_000
    )
    unbounded.add_assistant("", tool_calls=big_calls)
    with pytest.raises(ContextUnfit):
        unbounded.build(step=1)

    bounded = make_assembler(
        max_prompt_tokens=2_000, summarise_above_tokens=1_500, max_call_chars=500
    )
    bounded.add_assistant("", tool_calls=big_calls)
    assembled = bounded.build(step=1)
    assert assembled.record.estimated_tokens <= 2_000
    assert assembled.record.dropped_messages == 0, "nothing needed dropping"
