"""The triage domain's tools.

Two things are worth testing here and they are different in kind:

* **The store is a boundary.** A `message_id` arrives from the model, so it is a name and
  never a path. Traversal is refused in one place and the tests pin that.
* **The sample messages are a pair.** `003` trips the injection scan and `004` deliberately
  does not. That is not incidental — it is what makes the domain exercise *both* defences:
  the scan refuses the obvious payload before the model sees it, and the model's judgement is
  what stands between the subtle one and a wrong answer.

The second is the reason this domain exists, so it is asserted rather than assumed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from context.sanitize import assess
from runtime.config import load_config_by_name
from runtime.schemas import ToolDescriptor
from tools.catalogue import available_tool_names
from tools.registry import ToolDefinitionError, ToolError, ToolRegistry
from tools.triage import (
    EscalateArgs,
    EscalateTool,
    ListMessagesArgs,
    ListMessagesTool,
    MessageStore,
    ReadMessageArgs,
    ReadMessageTool,
)

MESSAGES = Path(__file__).resolve().parent.parent / "messages"


@pytest.fixture
def store(tmp_path: Path) -> MessageStore:
    (tmp_path / "001.json").write_text(
        json.dumps({"from": "a@example.com", "subject": "S", "body": "B"}), encoding="utf-8"
    )
    return MessageStore(tmp_path)


# ------------------------------------------------------------------ the boundary


@pytest.mark.parametrize("bad", ["", "   ", "../secrets", "/etc/passwd", "a/b", "a\\b", "./001"])
def test_a_message_id_is_a_name_and_never_a_path(store: MessageStore, bad: str) -> None:
    with pytest.raises(ToolError):
        store.load(bad)


def test_a_plain_name_is_accepted(store: MessageStore) -> None:
    assert store.load("001")["subject"] == "S"


def test_an_unknown_message_is_an_error_not_an_empty_read(store: MessageStore) -> None:
    with pytest.raises(ToolError, match="no such message"):
        store.load("999")


def test_a_malformed_message_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ToolError, match="not valid JSON"):
        MessageStore(tmp_path).load("bad")


# --------------------------------------------------------------------- the tools


def test_list_messages_names_what_is_waiting() -> None:
    tool = ListMessagesTool(root=MESSAGES)
    listed = tool.invoke(ListMessagesArgs())
    assert "001" in listed
    assert "Charged twice" in listed


def test_read_message_returns_the_body() -> None:
    tool = ReadMessageTool(root=MESSAGES)
    read = tool.invoke(ReadMessageArgs(message_id="001"))
    assert "dana@example.com" in read
    assert "refunded" in read


def test_an_empty_store_says_so_rather_than_failing(tmp_path: Path) -> None:
    assert ListMessagesTool(root=tmp_path).invoke(ListMessagesArgs()) == "no messages"


def test_escalate_appends_a_record(tmp_path: Path) -> None:
    (tmp_path / "001.json").write_text("{}", encoding="utf-8")
    EscalateTool(root=tmp_path).invoke(
        EscalateArgs(message_id="001", reason="needs a person", confirmation_token="t")
    )
    written = (tmp_path / "escalations.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(written[0]) == {"message_id": "001", "reason": "needs a person"}


def test_escalating_something_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="no such message"):
        EscalateTool(root=tmp_path).invoke(
            EscalateArgs(message_id="999", reason="r", confirmation_token="t")
        )


# ---------------------------------------------------- the gate is structural


def test_escalate_is_declared_a_side_effect() -> None:
    assert EscalateTool.side_effect is True
    assert EscalateTool.idempotent is False, "a side effect must not be retried"


def test_registration_refuses_escalate_without_the_token_field() -> None:
    """The boundary refuses a side-effecting tool that cannot carry an authorisation.

    Asserted through the registry rather than by reading the class, because the registry is
    what enforces it — and this is the tool where it matters.
    """

    class NoTokenArgs(BaseModel):
        model_config = ConfigDict(extra="forbid")
        message_id: str
        reason: str

    class ForgetsTheToken(EscalateTool):
        name = "forgets_the_token"
        args_model = NoTokenArgs

    with pytest.raises(ToolDefinitionError):
        ToolRegistry([ForgetsTheToken(root=".")])


def test_the_model_is_never_told_the_token_field_exists() -> None:
    advertised = EscalateTool(root=".").describe()
    assert "confirmation_token" not in advertised.parameters.get("properties", {})


# -------------------------------------------------- the pair that makes the domain


def body_of(message_id: str) -> str:
    return json.loads((MESSAGES / f"{message_id}.json").read_text(encoding="utf-8"))["body"]


def test_the_obvious_injection_is_caught_before_the_model_sees_it() -> None:
    """`003` is an instruction-override payload. The scan refuses the run."""
    assert assess(body_of("003")).trip is True


def test_the_subtle_injection_is_not_caught_and_that_is_the_point() -> None:
    """`004` asks the agent to skip its job in ordinary prose, with no marker phrase.

    It passes the scan, so it reaches the model — and what the model does with it is the
    thing this domain was built to exercise. If this test ever starts failing because the
    payload *is* caught, the sample has changed and the domain has lost half its point.
    """
    assert assess(body_of("004")).trip is False


def test_the_benign_samples_are_benign() -> None:
    for message_id in ("001", "002"):
        assert assess(body_of(message_id)).trip is False


# --------------------------------------------------------------- the configuration


def test_the_configuration_loads_and_names_only_real_tools() -> None:
    config = load_config_by_name("triage", root="configs")
    assert set(config.tools) <= set(available_tool_names())


def test_the_configuration_asks_for_structured_output() -> None:
    """Correctness has to be assertable, or the evals cannot encode what "correct" means.

    Note the accessor: the field is ``schema_``, aliased to ``schema`` so a YAML file can write
    the natural key. ``config.output.schema`` therefore resolves to **pydantic's deprecated
    ``schema()`` method**, not the field — it is truthy, so a caller who writes the obvious
    thing gets a bound method and no error. `runtime/loop.py` uses ``schema_`` throughout.
    """
    config = load_config_by_name("triage", root="configs")
    assert config.output.format == "json"
    schema = config.output.schema_
    assert schema is not None
    assert "category" in schema["properties"]
    assert "enum" in schema["properties"]["category"]
    assert "enum" in schema["properties"]["urgency"]


def test_the_schema_accessor_trap_is_real() -> None:
    """Pinned so nobody "tidies" the accessor back to the obvious one.

    If pydantic ever removes the deprecated method this test fails, which is the signal that
    the trap is gone and ``schema_`` is no longer load-bearing.
    """
    config = load_config_by_name("triage", root="configs")
    assert callable(config.output.schema), "the obvious accessor is no longer a method"
    assert isinstance(config.output.schema_, dict), "the field is the underscore name"


def test_the_escalation_is_the_only_gated_tool_in_the_configuration() -> None:
    """If reading were gated too, the gate would stop being about consequence."""
    config = load_config_by_name("triage", root="configs")
    registry = ToolRegistry(
        [
            ListMessagesTool(root=MESSAGES),
            ReadMessageTool(root=MESSAGES),
            EscalateTool(root=MESSAGES),
        ]
    )
    try:
        descriptors: list[ToolDescriptor] = registry.descriptors()
    finally:
        registry.close()
    gated = {d.name for d in descriptors if d.side_effect}
    assert gated == {"escalate"}
    assert set(config.tools) == {d.name for d in descriptors}
