"""Declaring, validating, and dispatching tools.

No tool implementation lives here. This module owns three things and nothing else:
the invariants a tool must satisfy to be registered, the argument validation that
guards every invocation, and the dispatch policy for one logical tool call —
including its attempts.

The retry policy lives here rather than in the loop because it is a property of the
tool boundary, not of orchestration. The loop decides whether the *run* can continue;
the registry decides how many times to *try*. That split is what makes replay exact:
a recorded call is replayed as its recorded outcome sequence, never re-decided.
"""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from runtime.schemas import Spend, ToolCallRecord, ToolCallRequest, ToolDescriptor, ToolResult
from runtime.status import Guardrail, ToolOutcome

#: The most attempts any single logical tool call may consume: initial, one repair
#: for malformed output, one retry for a transient error. Bounded by construction.
MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 0.05


class ToolDefinitionError(Exception):
    """A tool that cannot be registered. Raised at registration, never at call time."""


class ToolError(Exception):
    """A tool ran and failed.

    ``spend`` is for a tool that failed *after* spending. A delegated run that came back
    ``partial`` cost its caller real money, and a record that says otherwise understates the
    run — the same defect [decisions/0031](../../docs/decisions/0031-the-spend-is-in-the-record.md)
    exists to close, one hop further along. Defaulted, so an ordinary tool error is unchanged.
    """

    def __init__(self, *args: object, spend: Spend | None = None) -> None:
        super().__init__(*args)
        self.spend = spend or Spend()


class ToolTimeout(Exception):
    """A tool declares itself timed out. The executor can also produce this."""


class ToolMalformed(Exception):
    """A tool returned something that is not the shape it declared."""


class UnknownTool(Exception):
    """A requested tool is not in the registry."""


class Tool(ABC):
    """One tool: schema, side-effect class, implementation.

    Subclasses declare metadata as class variables and implement ``invoke``. Nothing
    else is required, and nothing outside the declared scope is reachable.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    args_model: ClassVar[type[BaseModel]]
    #: If set, the returned string must be JSON validating against this model.
    result_model: ClassVar[type[BaseModel] | None] = None
    side_effect: ClassVar[bool] = False
    idempotent: ClassVar[bool] = True
    #: A required tool that fails makes the run ``partial``; an optional one is skipped.
    optional: ClassVar[bool] = True
    timeout_s: ClassVar[float] = 10.0
    #: The field a side-effecting tool must declare. Never advertised to the model.
    confirmation_field: ClassVar[str] = "confirmation_token"

    @abstractmethod
    def invoke(self, args: BaseModel) -> str | ToolResult:
        """Run the tool and return its result as text, or as text plus what it spent.

        Raise ``ToolError``, ``ToolTimeout``, or ``ToolMalformed`` to classify a
        failure. Any other exception is reported as a generic tool error.
        """

    def describe(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description=self.description,
            parameters=_public_schema(self),
            side_effect=self.side_effect,
            idempotent=self.idempotent,
            optional=self.optional,
        )


def _public_schema(tool: Tool) -> dict[str, Any]:
    """The argument schema as the model sees it.

    For a side-effecting tool the confirmation field is stripped, including from
    ``required``. The model is not told the field exists, so it cannot be tempted to
    supply one — and if it supplies one anyway, dispatch overwrites it.
    """
    schema = tool.args_model.model_json_schema()
    if not tool.side_effect:
        return schema
    schema = dict(schema)
    properties = dict(schema.get("properties") or {})
    properties.pop(tool.confirmation_field, None)
    schema["properties"] = properties
    required = [name for name in (schema.get("required") or []) if name != tool.confirmation_field]
    if required:
        schema["required"] = required
    else:
        schema.pop("required", None)
    return schema


class ToolRegistry:
    """A validated set of tools, plus the dispatch policy for calling one."""

    def __init__(
        self,
        tools: Iterable[Tool] = (),
        *,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        monotonic: Callable[[], float] = time.monotonic,
        max_workers: int = 4,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._sleep = sleep
        self._jitter = jitter
        self._monotonic = monotonic
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tool")
        for tool in tools:
            self.register(tool)

    # -- declaration ----------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """Register a tool, or refuse it. Every invariant is checked here."""
        if not tool.name:
            raise ToolDefinitionError(f"{type(tool).__name__} declares no name")
        if tool.name in self._tools:
            raise ToolDefinitionError(f"duplicate tool name: {tool.name!r}")
        if not tool.description.strip():
            raise ToolDefinitionError(f"{tool.name}: a tool must describe itself")
        args_model = getattr(tool, "args_model", None)
        if not (isinstance(args_model, type) and issubclass(args_model, BaseModel)):
            raise ToolDefinitionError(f"{tool.name}: args_model must be a pydantic BaseModel")
        if tool.timeout_s <= 0:
            raise ToolDefinitionError(f"{tool.name}: timeout_s must be positive")

        if tool.side_effect:
            field = args_model.model_fields.get(tool.confirmation_field)
            if field is None:
                raise ToolDefinitionError(
                    f"{tool.name}: declares side_effect=True but its args model has no "
                    f"{tool.confirmation_field!r} field. A mutating tool that cannot carry "
                    f"a confirmation token cannot be registered."
                )
            if not field.is_required():
                raise ToolDefinitionError(
                    f"{tool.name}: {tool.confirmation_field!r} must be required, not defaulted"
                )
        elif not tool.idempotent:
            raise ToolDefinitionError(
                f"{tool.name}: a tool with no side effect is idempotent by definition"
            )

        # **A tool that cannot describe itself cannot be registered.** The descriptor is built from
        # the args model and is sent on every request, so a tool whose schema cannot be rendered is
        # a run that dies at setup — before anything is recorded, where the failure is hardest to
        # attribute. Proving it here puts the defect with the other registration invariants, which
        # is where a tool author is already looking.
        try:
            tool.describe()
        except Exception as exc:
            raise ToolDefinitionError(
                f"{tool.name}: describe() raised {type(exc).__name__}: {exc}. The descriptor is "
                f"part of every prompt, so a tool that cannot produce one cannot be registered."
            ) from exc
        self._tools[tool.name] = tool

    # -- inspection -----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def require(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownTool(name)
        return tool

    def describe(self, name: str) -> ToolDescriptor:
        return self.require(name).describe()

    def descriptors(self) -> list[ToolDescriptor]:
        return [self._tools[name].describe() for name in self.names()]

    # -- dispatch -------------------------------------------------------------

    def dispatch(
        self,
        request: ToolCallRequest,
        *,
        step: int,
        confirmation_token: str | None = None,
    ) -> ToolCallRecord:
        """Handle one logical tool call, including its attempts.

        **Never raises for a tool-level problem**: every failure becomes a record, including a
        tool's args model raising something pydantic did not wrap, and the executor refusing the
        work.

        What it deliberately does *not* cover is a **composition error** — the injected clock or
        the backoff ``sleep`` raising. Those are the caller's own broken callables rather than the
        tool's, and the registry cannot file them as a tool record any more than a run can be
        failed by a clock that does not exist. `runtime/loop.py` absorbs them at its seam, so a run
        still ends with a status instead of an exception — see decisions/0034.
        """
        tool = self._tools.get(request.name)
        if tool is None:
            return self._refused(
                request,
                step=step,
                error=f"unknown_tool: {request.name!r} is not available",
            )

        arguments = dict(request.arguments)
        confirmation_applied = False
        if tool.side_effect:
            if not confirmation_token:
                return self._refused(
                    request,
                    step=step,
                    error=(
                        f"confirmation_required: {tool.name!r} changes state and no "
                        f"confirmation token was supplied by the caller"
                    ),
                    guardrail=Guardrail.CONFIRMATION_MISSING,
                )
            # Overwrite whatever the model supplied. The principal decides this value,
            # not the model, and not a prompt instruction.
            arguments[tool.confirmation_field] = confirmation_token
            confirmation_applied = True

        started = self._monotonic()
        outcomes: list[ToolOutcome] = []
        result: str | None = None
        error: str | None = None
        #: Attempts add up. A call that failed and then succeeded was paid for twice, and only
        #: the successful attempt's spend would be reported if this were assignment.
        spend = Spend()
        repairs_left = 1
        retries_left = 1

        while len(outcomes) < MAX_ATTEMPTS:
            outcome, result, error, attempt_spend = self._attempt(tool, arguments)
            spend = spend + attempt_spend
            outcomes.append(outcome)
            if outcome is ToolOutcome.OK:
                break
            if outcome is ToolOutcome.NOT_EXECUTED:
                break  # argument validation failed; identical input cannot do better
            if outcome is ToolOutcome.MALFORMED and repairs_left > 0:
                repairs_left -= 1
                continue
            if (
                outcome in {ToolOutcome.ERROR, ToolOutcome.TIMEOUT}
                and tool.idempotent
                and retries_left > 0
            ):
                # Only an idempotent tool is retried. Retrying a mutating tool that
                # timed out could apply the mutation twice.
                retries_left -= 1
                self._backoff(len(outcomes))
                continue
            break

        return ToolCallRecord(
            step=step,
            name=request.name,
            arguments=request.arguments,
            outcome=outcomes[-1],
            attempts=len(outcomes),
            attempt_outcomes=outcomes,
            duration_s=round(self._monotonic() - started, 6),
            spend=spend,
            result=result,
            error=error,
            confirmation_applied=confirmation_applied,
        )

    # -- internals ------------------------------------------------------------

    def _attempt(
        self,
        tool: Tool,
        arguments: dict[str, Any],
    ) -> tuple[ToolOutcome, str | None, str | None, Spend]:
        """One attempt, as ``(outcome, result, error, spend)``.

        ``spend`` is reported on *both* ways an attempt can finish, because both can spend: a
        tool that returns reports it on its result, and a tool that raises reports it on the
        exception. A call that spent and then failed is not a call that spent nothing.

        A timeout reports zero. The worker thread is not killable, so whatever it went on to
        spend is discarded along with its result
        ([decisions/0007](../../docs/decisions/0007-in-process-tool-timeouts.md)) — and an
        unreported spend is better than a guessed one.
        """
        try:
            validated = tool.args_model.model_validate(arguments)
        except ValidationError as exc:
            return ToolOutcome.NOT_EXECUTED, None, f"invalid_arguments: {_brief(exc)}", Spend()
        except Exception as exc:
            # A tool's own schema code raising something pydantic did not wrap — a `TypeError` out
            # of a `field_validator`, say. It is the tool's defect, so it is the tool's record, and
            # `dispatch` keeps its promise not to raise for a tool-level problem.
            return (
                ToolOutcome.NOT_EXECUTED,
                None,
                f"invalid_arguments: the args model raised {type(exc).__name__}: {exc}",
                Spend(),
            )

        try:
            future = self._executor.submit(tool.invoke, validated)
        except Exception as exc:
            # The pool is gone — the registry was closed, or it refused the work. The registry owns
            # the executor, so this is a failure for *it* to report rather than for the caller to
            # catch, and a caller who closed the registry has already stopped expecting records.
            return (
                ToolOutcome.ERROR,
                None,
                f"the tool could not be dispatched: {type(exc).__name__}: {exc}",
                Spend(),
            )
        try:
            raw = future.result(timeout=tool.timeout_s)
        except FutureTimeout:
            # The worker thread is not killable. Its result is discarded and the run
            # proceeds; see docs/decisions/0007-in-process-tool-timeouts.md.
            future.cancel()
            return ToolOutcome.TIMEOUT, None, f"timeout after {tool.timeout_s}s", Spend()
        except ToolTimeout as exc:
            return (
                ToolOutcome.TIMEOUT,
                None,
                str(exc) or f"timeout after {tool.timeout_s}s",
                Spend(),
            )
        except ToolMalformed as exc:
            return ToolOutcome.MALFORMED, None, f"malformed_result: {exc}", Spend()
        except ToolError as exc:
            return ToolOutcome.ERROR, None, f"tool_error: {exc}", exc.spend
        except Exception as exc:
            return ToolOutcome.ERROR, None, f"{type(exc).__name__}: {exc}", Spend()

        text, spend, wrong = _normalise_result(raw)
        if wrong:
            return (
                ToolOutcome.MALFORMED,
                None,
                f"malformed_result: expected str, got {wrong}",
                spend,
            )

        if tool.result_model is not None:
            try:
                tool.result_model.model_validate_json(text)
            except (ValidationError, ValueError) as exc:
                return (
                    ToolOutcome.MALFORMED,
                    None,
                    f"malformed_result: does not satisfy {tool.result_model.__name__}: {exc}",
                    spend,
                )
        return ToolOutcome.OK, text, None, spend

    def _backoff(self, attempt: int) -> None:
        """Jittered exponential backoff between retries."""
        base = _BACKOFF_BASE_S * (2 ** (attempt - 1))
        self._sleep(base + self._jitter(0.0, base))

    def _refused(
        self,
        request: ToolCallRequest,
        *,
        step: int,
        error: str,
        guardrail: Guardrail | None = None,
    ) -> ToolCallRecord:
        return ToolCallRecord(
            step=step,
            name=request.name,
            arguments=request.arguments,
            outcome=ToolOutcome.NOT_EXECUTED,
            attempts=1,
            attempt_outcomes=[ToolOutcome.NOT_EXECUTED],
            duration_s=0.0,
            result=None,
            error=error,
            guardrail=guardrail,
        )

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def __enter__(self) -> ToolRegistry:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _brief(exc: ValidationError) -> str:
    """A one-line argument-validation message, field names first."""
    parts = []
    for error in exc.errors()[:4]:
        location = ".".join(str(item) for item in error["loc"]) or "<root>"
        parts.append(f"{location}: {error['msg']}")
    extra = "" if len(exc.errors()) <= 4 else f" (+{len(exc.errors()) - 4} more)"
    return "; ".join(parts) + extra


def _normalise_result(raw: Any) -> tuple[str | None, Spend, str]:
    """What a tool returned, as ``(text, spend, wrong_type)``.

    Two shapes are accepted — a bare string, and a string plus a spend. ``wrong_type`` names
    what was wrong when neither applies, and is empty when the result is usable.

    ``ToolResult.text`` is checked as well as the raw value: wrapping an answer has not earned
    an exemption from returning text, and a `ToolResult` carrying an `int` is exactly as
    malformed as a bare `int`. Checking it *before* the ``result_model`` branch also keeps one
    message for one problem. Handed a non-string, `model_validate_json` reports *"JSON input
    should be string, bytes or bytearray"* — true, and about the parser rather than the tool.
    """
    if isinstance(raw, ToolResult):
        if isinstance(raw.text, str):
            return raw.text, raw.spend, ""
        return None, raw.spend, type(raw.text).__name__
    if isinstance(raw, str):
        return raw, Spend(), ""
    return None, Spend(), type(raw).__name__


__all__ = [
    "MAX_ATTEMPTS",
    "Tool",
    "ToolDefinitionError",
    "ToolError",
    "ToolMalformed",
    "ToolRegistry",
    "ToolTimeout",
    "UnknownTool",
]
