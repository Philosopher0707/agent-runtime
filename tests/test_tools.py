"""The tool boundary: what may be registered, and how one call is dispatched.

Two separate claims are tested here. First, that an unsafe tool *cannot be registered* —
the invariants are structural, not documentary. Second, that the dispatch policy is what
the taxonomy says it is: how many attempts, in what order, and which outcomes are never
passed downstream.
"""

from __future__ import annotations

import time

import pytest
from pydantic import BaseModel, ConfigDict

from runtime.schemas import ToolCallRequest
from runtime.status import Guardrail, ToolOutcome
from tools.registry import (
    Tool,
    ToolDefinitionError,
    ToolError,
    ToolMalformed,
    ToolRegistry,
    ToolTimeout,
)
from tools.scripted import ScriptedTool


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UnconfirmedArgs(BaseModel):
    """A mutating tool's args with no confirmation field at all."""

    model_config = ConfigDict(extra="forbid")
    text: str


class DefaultedConfirmationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    confirmation_token: str = ""


class ValueArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int


class FixedTool(Tool):
    name = "fixed"
    description = "Returns a fixed payload."
    args_model = EmptyArgs

    def __init__(self, payload: str) -> None:
        self.payload = payload

    def invoke(self, args: BaseModel) -> str:
        return self.payload


class ReportingTool(FixedTool):
    name = "reporting"
    description = "Returns JSON that must satisfy Report."
    result_model = Report


class SlowTool(Tool):
    name = "slow"
    description = "Sleeps past its own timeout."
    args_model = EmptyArgs
    timeout_s = 0.05

    def invoke(self, args: BaseModel) -> str:
        time.sleep(0.3)
        return "too late"


class ToolAlpha(FixedTool):
    name = "alpha"
    description = "The first of two."


class ToolBeta(FixedTool):
    name = "beta"
    description = "The second of two."


def registry(*tools: Tool) -> ToolRegistry:
    return ToolRegistry(tools, sleep=lambda _s: None, jitter=lambda _a, _b: 0.0)


# ------------------------------------------------------------ registration rules


def test_side_effecting_tool_without_a_confirmation_field_cannot_be_registered() -> None:
    class Mutating(Tool):
        name = "mutating"
        description = "Changes state without declaring a confirmation field."
        args_model = UnconfirmedArgs
        side_effect = True
        idempotent = False

        def invoke(self, args: BaseModel) -> str:  # pragma: no cover - never reached
            return "should not run"

    with pytest.raises(ToolDefinitionError, match="confirmation_token"):
        registry(Mutating())


def test_confirmation_field_must_be_required_not_defaulted() -> None:
    class Mutating(Tool):
        name = "mutating"
        description = "Declares a confirmation field that is optional."
        args_model = DefaultedConfirmationArgs
        side_effect = True
        idempotent = False

        def invoke(self, args: BaseModel) -> str:  # pragma: no cover - never reached
            return "should not run"

    with pytest.raises(ToolDefinitionError, match="must be required"):
        registry(Mutating())


def test_a_pure_tool_cannot_declare_itself_non_idempotent() -> None:
    class Confused(Tool):
        name = "confused"
        description = "No side effect, yet claims not to be idempotent."
        args_model = EmptyArgs
        side_effect = False
        idempotent = False

        def invoke(self, args: BaseModel) -> str:  # pragma: no cover - never reached
            return ""

    with pytest.raises(ToolDefinitionError, match="idempotent by definition"):
        registry(Confused())


def test_duplicate_names_are_refused() -> None:
    with pytest.raises(ToolDefinitionError, match="duplicate tool name"):
        registry(FixedTool("a"), FixedTool("b"))


def test_a_tool_must_describe_itself() -> None:
    class Silent(FixedTool):
        name = "silent"
        description = "   "

    with pytest.raises(ToolDefinitionError, match="describe itself"):
        registry(Silent("x"))


def test_a_non_positive_timeout_is_refused() -> None:
    class Instant(FixedTool):
        name = "instant"
        timeout_s = 0.0

    with pytest.raises(ToolDefinitionError, match="timeout_s"):
        registry(Instant("x"))


# -------------------------------------------------------------------- dispatch


def test_unknown_tool_is_not_executed() -> None:
    with registry(FixedTool("ok")) as reg:
        record = reg.dispatch(ToolCallRequest(name="ghost"), step=1)
    assert record.outcome is ToolOutcome.NOT_EXECUTED
    assert record.attempts == 1
    assert "unknown_tool" in (record.error or "")


def test_invalid_arguments_are_not_retried() -> None:
    with registry(FixedTool("ok")) as reg:
        record = reg.dispatch(ToolCallRequest(name="fixed", arguments={"unexpected": 1}), step=1)
    assert record.outcome is ToolOutcome.NOT_EXECUTED
    assert record.attempts == 1
    assert "invalid_arguments" in (record.error or "")


def test_a_mutating_call_without_a_token_is_refused_before_execution() -> None:
    tool = ScriptedTool(
        name="writer", behaviour="ok", side_effect=True, idempotent=False, optional=False
    )
    with registry(tool) as reg:
        record = reg.dispatch(ToolCallRequest(name="writer", arguments={"a": 1}), step=1)
    assert record.outcome is ToolOutcome.NOT_EXECUTED
    assert record.guardrail is Guardrail.CONFIRMATION_MISSING
    assert record.confirmation_applied is False
    assert tool.calls == []


def test_the_principals_token_overwrites_whatever_the_model_supplied() -> None:
    tool = ScriptedTool(
        name="writer", behaviour="ok", side_effect=True, idempotent=False, optional=False
    )
    with registry(tool) as reg:
        record = reg.dispatch(
            ToolCallRequest(
                name="writer",
                arguments={"a": 1, "confirmation_token": "model-supplied"},
            ),
            step=1,
            confirmation_token="from-the-principal",
        )
    assert record.outcome is ToolOutcome.OK
    assert record.confirmation_applied is True
    # The record keeps what the model asked for...
    assert record.arguments["confirmation_token"] == "model-supplied"
    # ...while the tool received the principal's value.
    assert tool.calls[0]["confirmation_token"] == "from-the-principal"


def test_an_idempotent_tool_is_retried_once() -> None:
    tool = ScriptedTool(name="flaky", behaviour="error_then_ok")
    with registry(tool) as reg:
        record = reg.dispatch(ToolCallRequest(name="flaky"), step=1)
    assert record.outcome is ToolOutcome.OK
    assert record.attempts == 2
    assert record.attempt_outcomes == [ToolOutcome.ERROR, ToolOutcome.OK]
    assert len(tool.calls) == 2


def test_a_non_idempotent_tool_is_never_retried() -> None:
    tool = ScriptedTool(
        name="writer", behaviour="error", side_effect=True, idempotent=False, optional=False
    )
    with registry(tool) as reg:
        record = reg.dispatch(ToolCallRequest(name="writer"), step=1, confirmation_token="tok")
    assert record.outcome is ToolOutcome.ERROR
    assert record.attempts == 1
    assert len(tool.calls) == 1


def test_a_malformed_result_earns_exactly_one_repair() -> None:
    tool = ScriptedTool(name="flaky", behaviour="malformed")
    with registry(tool) as reg:
        record = reg.dispatch(ToolCallRequest(name="flaky"), step=1)
    assert record.outcome is ToolOutcome.MALFORMED
    assert record.attempts == 2
    assert len(tool.calls) == 2


def test_attempts_are_bounded() -> None:
    """Whatever the tool does, one logical call cannot consume the run."""
    tool = ScriptedTool(name="flaky", behaviour="error")
    with registry(tool) as reg:
        record = reg.dispatch(ToolCallRequest(name="flaky"), step=1)
    assert record.attempts <= 3


def test_a_result_that_violates_its_declared_model_is_malformed() -> None:
    with registry(ReportingTool('{"total": 1}')) as reg:
        good = reg.dispatch(ToolCallRequest(name="reporting"), step=1)
    assert good.outcome is ToolOutcome.OK

    with registry(ReportingTool('{"total": "not an integer"}')) as reg:
        bad = reg.dispatch(ToolCallRequest(name="reporting"), step=1)
    assert bad.outcome is ToolOutcome.MALFORMED
    assert bad.result is None
    assert "Report" in (bad.error or "")


def test_a_non_string_result_is_malformed() -> None:
    class WrongType(Tool):
        name = "wrong"
        description = "Returns an int from a tool that must return text."
        args_model = EmptyArgs

        def invoke(self, args: BaseModel) -> str:
            return 42  # type: ignore[return-value]

    with registry(WrongType()) as reg:
        record = reg.dispatch(ToolCallRequest(name="wrong"), step=1)
    assert record.outcome is ToolOutcome.MALFORMED
    assert "expected str" in (record.error or "")


def test_a_tool_that_overruns_its_timeout_is_timed_out() -> None:
    with registry(SlowTool()) as reg:
        record = reg.dispatch(ToolCallRequest(name="slow"), step=1)
    assert record.outcome is ToolOutcome.TIMEOUT
    assert "timeout after" in (record.error or "")


def test_a_tool_that_declares_itself_timed_out_is_timed_out() -> None:
    class Declaring(Tool):
        name = "declaring"
        description = "Raises ToolTimeout."
        args_model = EmptyArgs

        def invoke(self, args: BaseModel) -> str:
            raise ToolTimeout("gave up waiting")

    with registry(Declaring()) as reg:
        record = reg.dispatch(ToolCallRequest(name="declaring"), step=1)
    assert record.outcome is ToolOutcome.TIMEOUT
    assert record.error == "gave up waiting"


def test_an_unexpected_exception_is_a_tool_error_not_a_crash() -> None:
    class Exploding(Tool):
        name = "exploding"
        description = "Raises something it did not declare."
        args_model = EmptyArgs

        def invoke(self, args: BaseModel) -> str:
            raise KeyError("surprise")

    with registry(Exploding()) as reg:
        record = reg.dispatch(ToolCallRequest(name="exploding"), step=1)
    assert record.outcome is ToolOutcome.ERROR
    assert "KeyError" in (record.error or "")


def test_tool_error_and_malformed_are_distinguishable() -> None:
    class Failing(Tool):
        name = "failing"
        description = "Raises ToolError."
        args_model = EmptyArgs

        def invoke(self, args: BaseModel) -> str:
            raise ToolError("nope")

    class Mangled(Tool):
        name = "mangled"
        description = "Raises ToolMalformed."
        args_model = EmptyArgs

        def invoke(self, args: BaseModel) -> str:
            raise ToolMalformed("not the right shape")

    with registry(Failing(), Mangled()) as reg:
        assert reg.dispatch(ToolCallRequest(name="failing"), step=1).outcome is ToolOutcome.ERROR
        assert (
            reg.dispatch(ToolCallRequest(name="mangled"), step=1).outcome is ToolOutcome.MALFORMED
        )


# ------------------------------------------------------------------ descriptors


def test_the_confirmation_field_is_never_advertised_to_the_model() -> None:
    """The model is not told the field exists, so it cannot be tempted to supply one."""
    tool = ScriptedTool(
        name="writer", behaviour="ok", side_effect=True, idempotent=False, optional=False
    )
    with registry(tool) as reg:
        described = reg.describe("writer")
    assert "confirmation_token" not in described.parameters.get("properties", {})
    assert "confirmation_token" not in (described.parameters.get("required") or [])


def test_descriptors_are_ordered_and_complete() -> None:
    with registry(ToolAlpha("a"), ToolBeta("b")) as reg:
        names = [descriptor.name for descriptor in reg.descriptors()]
    assert names == ["alpha", "beta"]
