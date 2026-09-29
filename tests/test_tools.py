"""The tool boundary: what may be registered, and how one call is dispatched.

Two separate claims are tested here. First, that an unsafe tool *cannot be registered* —
the invariants are structural, not documentary. Second, that the dispatch policy is what
the taxonomy says it is: how many attempts, in what order, and which outcomes are never
passed downstream.
"""

from __future__ import annotations

import time

import pytest
from pydantic import BaseModel, ConfigDict, field_validator

from runtime.schemas import Spend, ToolCallRequest, ToolDescriptor, ToolResult
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


class ConfirmedArgs(BaseModel):
    """A mutating tool's args: a payload, and the required confirmation field."""

    model_config = ConfigDict(extra="forbid")
    text: str = "x"
    confirmation_token: str


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


# ---------------------------------------------------------------------- the spend

#: What a delegated run costs, in the two units the budget charges for. Small, and non-zero in
#: both — a test that only moved tokens would not notice a charge that lost the money.
CHILD_SPEND = Spend(tokens_total=120, cost_usd=0.004)


class SpendingTool(FixedTool):
    """A tool that reports what it consumed, the way a delegated run does."""

    name = "spending"
    description = "Returns text and a spend."

    def invoke(self, args: BaseModel) -> ToolResult:
        return ToolResult(text=self.payload, spend=CHILD_SPEND)


def test_a_tool_can_report_what_it_spent() -> None:
    """The channel has to carry a spend, or a tool that spends cannot be charged for it."""
    with registry(SpendingTool("answer")) as reg:
        record = reg.dispatch(ToolCallRequest(name="spending"), step=1)
    assert record.outcome is ToolOutcome.OK
    assert record.result == "answer"
    assert record.spend == CHILD_SPEND


def test_a_tool_that_reports_nothing_spent_nothing() -> None:
    """A bare string is the shape almost every tool returns, and it means "free"."""
    with registry(FixedTool("free")) as reg:
        record = reg.dispatch(ToolCallRequest(name="fixed"), step=1)
    assert record.spend.is_zero


def test_a_call_that_failed_after_spending_is_recorded_as_having_spent() -> None:
    """A delegated run that came back `partial` cost its caller real money.

    A record that says otherwise understates the run — and would make the parent's replay
    diverge from the parent, which is the defect decisions/0031 exists to close.

    Declared non-idempotent, like `spawn_agent` itself, so there is exactly one attempt and the
    spend is unambiguous.
    """

    class SpendingFailure(Tool):
        name = "spending_failure"
        description = "Spends, then fails. Never retried, because it is not idempotent."
        args_model = ConfirmedArgs
        side_effect = True
        idempotent = False

        def invoke(self, args: BaseModel) -> str:
            raise ToolError("the spawned run did not complete", spend=CHILD_SPEND)

    with registry(SpendingFailure()) as reg:
        record = reg.dispatch(
            ToolCallRequest(name="spending_failure", arguments={}),
            step=1,
            confirmation_token="tok",
        )
    assert record.outcome is ToolOutcome.ERROR
    assert record.attempts == 1
    assert record.spend == CHILD_SPEND


def test_every_attempts_spend_is_counted() -> None:
    """A call that failed and then succeeded was paid for twice.

    Assignment instead of addition would report only the successful attempt, and would do it
    silently — the total would simply be too small.
    """

    class FlakySpender(FixedTool):
        name = "flaky_spender"
        description = "Spends and fails once, then spends and succeeds."

        def __init__(self) -> None:
            super().__init__("ok")
            self.calls = 0

        def invoke(self, args: BaseModel) -> ToolResult:
            self.calls += 1
            if self.calls == 1:
                raise ToolError("first attempt", spend=CHILD_SPEND)
            return ToolResult(text="ok", spend=CHILD_SPEND)

    with registry(FlakySpender()) as reg:
        record = reg.dispatch(ToolCallRequest(name="flaky_spender"), step=1)
    assert record.attempts == 2
    assert record.spend == Spend(
        tokens_total=2 * CHILD_SPEND.tokens_total, cost_usd=2 * CHILD_SPEND.cost_usd
    )


def test_a_timed_out_call_reports_no_spend() -> None:
    """The worker thread is not killable, so whatever it went on to spend is discarded with its
    result (decisions/0007). An unreported spend is better than a guessed one."""
    with registry(SlowTool()) as reg:
        record = reg.dispatch(ToolCallRequest(name="slow"), step=1)
    assert record.outcome is ToolOutcome.TIMEOUT
    assert record.spend.is_zero


def test_a_result_wrapper_is_not_an_exemption_from_returning_text() -> None:
    """A `ToolResult` carrying an `int` is exactly as malformed as a bare `int`."""

    class NotText(FixedTool):
        name = "not_text"
        description = "Returns a ToolResult whose text is not a string."

        def invoke(self, args: BaseModel) -> ToolResult:
            return ToolResult(text=1)  # type: ignore[arg-type]

    with registry(NotText("x")) as reg:
        record = reg.dispatch(ToolCallRequest(name="not_text"), step=1)
    assert record.outcome is ToolOutcome.MALFORMED
    assert "expected str, got int" in (record.error or "")


def test_a_non_string_result_gets_one_message_with_or_without_a_result_model() -> None:
    """One problem, one message. With a declared `result_model`, the old order reached
    `model_validate_json`, which answered *"JSON input should be string, bytes or bytearray"* —
    true, and about the parser rather than about the tool that returned the wrong type."""

    class NonString(FixedTool):
        name = "non_string"
        description = "Declares a result_model and returns something that is not a string."
        result_model = Report

        def invoke(self, args: BaseModel) -> str:
            return 3  # type: ignore[return-value]

    with registry(NonString("x")) as reg:
        record = reg.dispatch(ToolCallRequest(name="non_string"), step=1)
    assert record.outcome is ToolOutcome.MALFORMED
    assert "expected str, got int" in (record.error or "")


# ------------------------------------------- the boundary keeps its own promise

#: `dispatch` says it never raises for a tool-level problem. These force the two places it did —
#: a tool's args model raising something pydantic does not wrap, and the executor refusing the
#: work — and then pin the line where absorbing stops, so the promise is bounded rather than
#: aspirational.


def test_a_tool_whose_args_model_raises_is_a_record_not_a_crash() -> None:
    """A `field_validator` raising a `TypeError` escapes pydantic's wrapping. It is the tool's own
    schema code, so it is the tool's record."""

    class ExplodingArgs(BaseModel):
        model_config = ConfigDict(extra="forbid")
        value: int = 1

        @field_validator("value")
        @classmethod
        def boom(cls, value: int) -> int:
            raise TypeError("a validator bug")

    class Exploding(Tool):
        name = "exploding_args"
        description = "Its args model raises."
        args_model = ExplodingArgs

        def invoke(self, args: BaseModel) -> str:  # pragma: no cover - never reached
            return "never"

    with registry(Exploding()) as reg:
        record = reg.dispatch(
            ToolCallRequest(name="exploding_args", arguments={"value": 1}), step=1
        )
    assert record.outcome is ToolOutcome.NOT_EXECUTED
    assert "TypeError" in (record.error or "")
    assert "a validator bug" in (record.error or "")


def test_dispatch_after_close_is_a_record_not_a_crash() -> None:
    """The registry owns the executor, so the pool being gone is a failure for *it* to report rather
    than for the caller to catch."""
    reg = registry(FixedTool("ok"))
    reg.close()
    record = reg.dispatch(ToolCallRequest(name="fixed"), step=1)
    assert record.outcome is ToolOutcome.ERROR
    assert "could not be dispatched" in (record.error or "")


def test_a_broken_clock_is_a_composition_error_not_a_tool_record() -> None:
    """Where absorbing stops, stated as a test rather than left to judgement.

    The clock is injected by the composition root, so a clock that raises is the caller's own broken
    callable, not the tool's — and the registry cannot file it as a tool record any more than a run
    can be failed by a clock that does not exist. `runtime/loop.py` absorbs it at its seam, so a run
    still ends with a status; that is decisions/0034's job, not this boundary's.
    """
    reg = ToolRegistry(
        [FixedTool("ok")],
        monotonic=lambda: (_ for _ in ()).throw(RuntimeError("the clock broke")),
    )
    with pytest.raises(RuntimeError, match="the clock broke"):
        reg.dispatch(ToolCallRequest(name="fixed"), step=1)


def test_a_tool_that_cannot_describe_itself_cannot_be_registered() -> None:
    """The descriptor is built from the args model and sent on every request, so a tool whose schema
    cannot be rendered is a run that dies at setup — before anything is recorded, which is the
    hardest place to attribute a failure. Proving it here puts the defect where a tool author is
    already looking."""

    class Undescribable(Tool):
        name = "undescribable"
        description = "Its descriptor cannot be built."
        args_model = EmptyArgs

        def invoke(self, args: BaseModel) -> str:  # pragma: no cover - never reached
            return "ok"

        def describe(self) -> ToolDescriptor:
            raise RuntimeError("no schema for you")

    with pytest.raises(ToolDefinitionError, match="cannot produce one"):
        registry(Undescribable())


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
