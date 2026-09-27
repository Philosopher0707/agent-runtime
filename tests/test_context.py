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
from runtime.schemas import ToolDescriptor


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
    assembler = make_assembler(
        system="S" * 10,
        task="T" * 10,
        max_prompt_tokens=10,
        summarise_above_tokens=250,
        summary_chars=40,
    )
    for index in range(3):
        assembler.add_tool_result(name=f"tool{index}", envelope="R" * 400)

    assembled = assembler.build(step=1)
    assert assembled.record.dropped_messages == 3
    assert assembled.messages[0]["content"] == "S" * 10
    assert assembled.messages[1]["content"] == "T" * 10


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
