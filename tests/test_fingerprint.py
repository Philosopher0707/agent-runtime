"""The prompt's identity, by component.

The property under test is **isolation**: a change to one part of the prompt moves that
part's hash and leaves the others alone. That is the whole difference from `prompt_hash`,
which is correct and unhelpful — it says the prompt changed and refuses to say what moved.

The other property is that the identity excludes the *transcript*. A prompt version should
not move because a tool returned a different number.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import context.assembler as assembler
import context.fingerprint as fingerprint_module
from context.fingerprint import PromptFingerprint, fingerprint
from runtime.config import load_config_by_name
from runtime.factory import build_tools
from runtime.trace import read_trace
from tests.helpers import execute, make_config, text

COMPONENTS = ("system_prompt", "tool_definitions", "envelope", "renderer")


def tools_for(config) -> list:
    registry = build_tools(config)
    try:
        return registry.descriptors()
    finally:
        registry.close()


@pytest.fixture
def base() -> PromptFingerprint:
    config = make_config()
    return fingerprint(system_prompt=config.system_prompt, tools=tools_for(config))


# ------------------------------------------------------------------ the components


def test_every_component_is_hashed_separately(base: PromptFingerprint) -> None:
    values = [getattr(base, name) for name in COMPONENTS]
    assert len(set(values)) == len(COMPONENTS), "two components share a hash"


def test_the_identity_is_not_one_of_its_parts(base: PromptFingerprint) -> None:
    assert base.identity not in [getattr(base, name) for name in COMPONENTS]


def test_the_dict_form_carries_everything(base: PromptFingerprint) -> None:
    assert set(base.as_dict()) == {*COMPONENTS, "identity"}


def test_the_fingerprint_is_frozen(base: PromptFingerprint) -> None:
    with pytest.raises(FrozenInstanceError):
        base.system_prompt = "x"  # type: ignore[misc]


# --------------------------------------------------------------------- isolation


def test_changing_the_system_prompt_moves_only_that_component() -> None:
    """The diagnosis a bare hash cannot give: *which* part changed."""
    config = make_config()
    tools = tools_for(config)
    before = fingerprint(system_prompt=config.system_prompt, tools=tools)
    after = fingerprint(system_prompt=config.system_prompt + " ", tools=tools)

    assert after.changed_from(before) == ["system_prompt"]
    assert after.identity != before.identity
    assert after.tool_definitions == before.tool_definitions
    assert after.envelope == before.envelope
    assert after.renderer == before.renderer


def test_changing_a_tool_description_moves_only_that_component() -> None:
    config = make_config()
    tools = tools_for(config)
    before = fingerprint(system_prompt=config.system_prompt, tools=tools)

    edited = list(tools)
    edited[0] = edited[0].model_copy(update={"description": edited[0].description + "."})
    after = fingerprint(system_prompt=config.system_prompt, tools=edited)

    assert after.changed_from(before) == ["tool_definitions"]
    assert after.identity != before.identity
    assert after.system_prompt == before.system_prompt


def test_adding_a_tool_moves_the_tool_component() -> None:
    config = make_config()
    tools = tools_for(config)
    before = fingerprint(system_prompt=config.system_prompt, tools=tools)
    after = fingerprint(system_prompt=config.system_prompt, tools=[*tools, tools[0]])
    assert "tool_definitions" in after.changed_from(before)


def test_tool_order_does_not_matter() -> None:
    """The component is sorted by name, so a reordering is not a change."""
    config = make_config()
    tools = tools_for(config)
    assert fingerprint(system_prompt=config.system_prompt, tools=tools).identity == (
        fingerprint(system_prompt=config.system_prompt, tools=list(reversed(tools))).identity
    )


def test_changing_the_envelope_moves_only_that_component(
    base: PromptFingerprint, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The envelope is *source*, not content — nothing else names it, which is the point."""
    monkeypatch.setattr(
        fingerprint_module, "UNTRUSTED_PREAMBLE", fingerprint_module.UNTRUSTED_PREAMBLE + "!"
    )
    config = make_config()
    after = fingerprint(system_prompt=config.system_prompt, tools=tools_for(config))
    assert after.changed_from(base) == ["envelope"]
    assert after.identity != base.identity


def test_changing_the_renderer_moves_only_that_component(
    base: PromptFingerprint, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tool-call format is inside every prompt and lives in the assembler."""
    original = assembler.render_tool_call
    monkeypatch.setattr(
        assembler, "render_tool_call", lambda name, args: original(name, args) + "!"
    )
    config = make_config()
    after = fingerprint(system_prompt=config.system_prompt, tools=tools_for(config))
    assert after.changed_from(base) == ["renderer"]
    assert after.identity != base.identity


def test_no_tools_is_a_valid_fingerprint() -> None:
    assert fingerprint(system_prompt="s", tools=[]).identity


# ------------------------------------------------- what the identity must ignore


def test_the_identity_ignores_the_transcript() -> None:
    """A prompt version must not move because a tool returned a different number."""
    config = make_config()
    tools = tools_for(config)
    first = fingerprint(system_prompt=config.system_prompt, tools=tools)

    # Nothing about the task or the transcript is an input, so this is structural — but the
    # property is worth stating, because a version that moved on every run would be useless.
    import inspect

    parameters = set(inspect.signature(fingerprint).parameters)
    assert parameters == {"system_prompt", "tools"}
    assert first.identity == fingerprint(system_prompt=config.system_prompt, tools=tools).identity


def test_the_same_configuration_produces_the_same_identity() -> None:
    config = load_config_by_name("default", root="configs")
    tools = tools_for(config)
    assert fingerprint(system_prompt=config.system_prompt, tools=tools).identity == (
        fingerprint(system_prompt=config.system_prompt, tools=tools).identity
    )


# ------------------------------------------------------------- recorded in the trace


def test_the_fingerprint_is_recorded_in_the_trace(tmp_path, tracer) -> None:
    """It has to be in the record, or it can only be computed for the *current* code."""
    execute("go", config=make_config(), tracer=tracer, script=[text("done")])
    recorded = read_trace(tracer.path).first("run_started").payload["prompt_fingerprint"]
    assert set(recorded) == {*COMPONENTS, "identity"}


def test_two_runs_of_the_same_configuration_agree(tmp_path, tracer) -> None:
    config = make_config()
    execute("first task", config=config, tracer=tracer, script=[text("a")])
    first = read_trace(tracer.path).first("run_started").payload["prompt_fingerprint"]

    from runtime.trace import TraceWriter, new_trace_id

    with TraceWriter(tmp_path / "second", new_trace_id()) as second_tracer:
        execute("a different task", config=config, tracer=second_tracer, script=[text("b")])
        second = read_trace(second_tracer.path).first("run_started").payload["prompt_fingerprint"]

    assert first["identity"] == second["identity"], "the identity moved with the task"


def test_the_per_step_hash_is_unchanged_by_this(tmp_path, tracer) -> None:
    """`prompt_hash` remains the authority on *whether* a prompt changed. Replay depends on
    it, and adding a diagnosis must not move it."""
    from providers.base import prompt_hash

    execute("go", config=make_config(), tracer=tracer, script=[text("done")])
    recorded = read_trace(tracer.path).first("run_finished").payload["prompt_hashes"]
    assert recorded == [
        prompt_hash(
            [
                {"role": "system", "content": make_config().system_prompt},
                {"role": "user", "content": "go"},
            ],
            tools_for(make_config()),
        )
    ]
