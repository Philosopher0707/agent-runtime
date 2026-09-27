"""The loop. Orchestration only: step, dispatch, terminate.

**The one rule.** No domain logic and no provider-specific code lives in this file.
If you are writing an ``if`` about a particular task, tool, or provider, stop: it
belongs in a configuration, in a tool, or in the model adapter.

What this module is allowed to know, and nothing else:

* the ``Provider`` protocol — not any adapter
* the tool boundary protocol — not any tool
* the budget, the tracer, the context assembler, the failure taxonomy

It knows one tool *name*: ``ask_clarification``, a control tool intercepted before
dispatch. That is protocol, not capability, and it is the only exception.

The division of labour with the tool boundary is deliberate. The registry decides
**how many times to try** a tool. The loop decides **whether the run can continue**.
Splitting it that way is what lets a recorded run be replayed exactly instead of
re-decided.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from context.assembler import ContextAssembler, ContextUnfit
from context.sanitize import assess, leaks_system_prompt, wrap_untrusted
from providers.base import Provider, ProviderError, prompt_hash
from runtime.budget import Budget, BudgetExceeded
from runtime.config import Configuration
from runtime.redact import Redactor
from runtime.schemas import (
    FailureEvent,
    ModelCallRecord,
    ModelResponse,
    RunOutput,
    ToolCallRecord,
    ToolCallRequest,
    ToolDescriptor,
)
from runtime.status import FailureClass, Guardrail, RunStatus, ToolOutcome
from runtime.structured import parse_structured
from runtime.trace import TraceWriter
from tools.builtin import CLARIFICATION_TOOL_NAME

#: Outcome -> the taxonomy row it belongs to. A total mapping, so no outcome can
#: arrive without a class.
_OUTCOME_TO_CLASS: dict[ToolOutcome, FailureClass] = {
    ToolOutcome.TIMEOUT: FailureClass.TOOL_TIMEOUT,
    ToolOutcome.ERROR: FailureClass.TOOL_ERROR,
    ToolOutcome.MALFORMED: FailureClass.TOOL_MALFORMED,
    ToolOutcome.NOT_EXECUTED: FailureClass.TOOL_ERROR,
    ToolOutcome.OK: FailureClass.NONE,
}


@runtime_checkable
class ToolBoundary(Protocol):
    """What the loop needs from the tool layer. Two methods, no implementations."""

    def descriptors(self) -> list[ToolDescriptor]: ...

    def dispatch(
        self,
        request: ToolCallRequest,
        *,
        step: int,
        confirmation_token: str | None = None,
    ) -> ToolCallRecord: ...


@dataclass
class _State:
    failures: list[FailureEvent] = field(default_factory=list)
    tool_records: list[ToolCallRecord] = field(default_factory=list)
    prompt_hashes: list[str] = field(default_factory=list)
    model_calls: int = 0
    output: str | None = None
    reason: str | None = None
    clarifying_question: str | None = None
    clarifications_suppressed: int = 0
    repairs_used: int = 0


class _Orchestrator:
    """One run's state and the six decisions the loop is allowed to make."""

    def __init__(
        self,
        *,
        task: str,
        config: Configuration,
        provider: Provider,
        tools: ToolBoundary,
        tracer: TraceWriter,
        budget: Budget | None,
        confirmation_token: str | None,
        clock: Callable[[], float],
    ) -> None:
        self.task = task
        self.config = config
        self.provider = provider
        self.tools = tools
        self.tracer = tracer
        self.clock = clock
        self.confirmation_token = confirmation_token
        self.budget = budget or Budget.from_config(config.budget, clock=clock)
        self.started = clock()
        self.state = _State()
        self._descriptors = tools.descriptors()
        self._by_name = {descriptor.name: descriptor for descriptor in self._descriptors}

    # -- the run --------------------------------------------------------------

    def run(self) -> RunOutput:
        # Redaction is a property of the record, not of the prompt: the model still sees
        # the real data. Enabled before the first event so a trace is never half redacted.
        redaction = self.config.guardrails.redaction
        if redaction.mode == "trace":
            self.tracer.enable_redaction(Redactor.from_names(redaction.patterns))

        self.tracer.run_started(
            task=self.task,
            config=self.config,
            provider_name=self.provider.name,
            model=self.provider.model,
            tools=self._descriptors,
        )

        preflight = self._preflight()
        if preflight is not None:
            return preflight

        assembler = ContextAssembler(
            system_prompt=self.config.system_prompt,
            task=self.task,
            config=self.config.context,
        )

        while True:
            try:
                step = self.budget.begin_step()
            except BudgetExceeded as exc:
                return self._stop(
                    FailureClass.BUDGET_EXHAUSTED, RunStatus.PARTIAL, exc.reason, reason=exc.reason
                )

            try:
                assembled = assembler.build(step=step)
            except ContextUnfit as exc:
                return self._stop(
                    FailureClass.CONTEXT_OVERFLOW,
                    RunStatus.PARTIAL,
                    str(exc),
                    step=step,
                    reason="context_overflow",
                )
            self.tracer.context(assembled.record)

            messages = assembled.messages
            self.state.prompt_hashes.append(prompt_hash(messages, self._descriptors))

            response, stop = self._model_call(step, messages)
            if stop is not None:
                return stop

            assert response is not None

            if response.refusal:
                # Verbatim. Not rephrased, not retried.
                self.state.output = response.text
                return self._stop(
                    FailureClass.MODEL_REFUSAL,
                    RunStatus.REFUSED,
                    "the model refused the task",
                    step=step,
                    reason="model_refusal",
                )

            if response.tool_calls:
                stop_reason = self._handle_tool_calls(response, step, assembler)
                if stop_reason is not None:
                    return self._finish(reason=stop_reason or None)
                continue

            stop_reason = self._handle_final_answer(response, step, assembler)
            if stop_reason is not None:
                return self._finish(reason=stop_reason or None)

    # -- preflight guardrails -------------------------------------------------

    def _preflight(self) -> RunOutput | None:
        """Guardrails that need no model call, so a bad task costs nothing."""
        guardrails = self.config.guardrails
        if not self.task.strip():
            return self._stop(
                FailureClass.GUARDRAIL_TRIP,
                RunStatus.REFUSED,
                "the task is empty",
                guardrail=Guardrail.EMPTY_INPUT,
                reason="guardrail:empty_input",
            )
        if len(self.task) > guardrails.max_input_chars:
            return self._stop(
                FailureClass.GUARDRAIL_TRIP,
                RunStatus.REFUSED,
                f"the task is {len(self.task)} characters, limit is {guardrails.max_input_chars}",
                guardrail=Guardrail.INPUT_TOO_LARGE,
                reason="guardrail:input_too_large",
            )
        return None

    # -- model ----------------------------------------------------------------

    def _model_call(
        self,
        step: int,
        messages: list[dict[str, Any]],
    ) -> tuple[ModelResponse | None, RunOutput | None]:
        started = self.clock()
        try:
            response = self.provider.complete(messages, self._descriptors)
        except ProviderError as exc:
            return None, self._stop(
                FailureClass.PROVIDER_ERROR,
                RunStatus.FAILED,
                str(exc),
                step=step,
                reason="provider_error",
            )
        latency = self.clock() - started

        self.state.model_calls += 1
        self.tracer.model_call(
            ModelCallRecord(
                step=step,
                provider=self.provider.name,
                model=self.provider.model,
                prompt_hash=self.state.prompt_hashes[-1],
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                latency_s=round(latency, 6),
                cost_usd=response.cost_usd,
                finish_reason=response.finish_reason,
                refusal=response.refusal,
                response=response,
            )
        )
        try:
            self.budget.add_usage(
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                cost_usd=response.cost_usd,
            )
        except BudgetExceeded as exc:
            return None, self._stop(
                FailureClass.BUDGET_EXHAUSTED,
                RunStatus.PARTIAL,
                exc.reason,
                step=step,
                reason=exc.reason,
            )

        if not response.tool_calls and not (response.text or "").strip():
            # A provider that returns neither content nor a call has not answered.
            self.state.output = ""
            return response, self._stop(
                FailureClass.PROVIDER_ERROR,
                RunStatus.DEGRADED,
                "the model returned neither text nor a tool call",
                step=step,
                reason="empty_model_turn",
            )
        return response, None

    # -- final answer ---------------------------------------------------------

    def _handle_final_answer(
        self,
        response: ModelResponse,
        step: int,
        assembler: ContextAssembler,
    ) -> str | None:
        """Return a stop reason, or None to take another step."""
        text = response.text or ""

        if leaks_system_prompt(
            text,
            self.config.system_prompt,
            prefix_chars=self.config.guardrails.disclosure_prefix_chars,
        ):
            return self._note_stop(
                FailureClass.GUARDRAIL_TRIP,
                RunStatus.REFUSED,
                "the answer echoes the system prompt",
                step=step,
                guardrail=Guardrail.SYSTEM_PROMPT_DISCLOSURE,
                reason="guardrail:system_prompt_disclosure",
            )

        if self.config.output.format == "text":
            self.state.output = text
            return "completed"

        parsed = parse_structured(text, self.config.output.schema_)
        if parsed.ok:
            self.state.output = text
            return "completed"

        if self.state.repairs_used < self.config.output.max_repair_attempts:
            self.state.repairs_used += 1
            assembler.add_assistant(text)
            assembler.add_runtime_note(_repair_note(parsed.error or "unusable", self.config))
            return None

        return self._note_stop(
            FailureClass.UNPARSEABLE_OUTPUT,
            RunStatus.FAILED,
            f"the answer is not usable after {self.state.repairs_used} repair attempt(s): "
            f"{parsed.error}",
            step=step,
            reason="unparseable_output",
        )

    # -- tools ----------------------------------------------------------------

    def _handle_tool_calls(
        self,
        response: ModelResponse,
        step: int,
        assembler: ContextAssembler,
    ) -> str | None:
        assembler.add_assistant(response.text, tool_calls=response.tool_calls)
        terminal: str | None = None
        saw_clarification = False

        for call in response.tool_calls:
            if call.name == CLARIFICATION_TOOL_NAME:
                # Scanned rather than short-circuited, so a second question in the same
                # turn is *counted* instead of silently dropped.
                self._handle_clarification(call, step)
                saw_clarification = True
                continue

            if saw_clarification or terminal is not None:
                # A question has been asked, or the run is already ending. The rest of
                # this turn's calls are not acted on.
                continue

            record = self.tools.dispatch(
                call, step=step, confirmation_token=self.confirmation_token
            )
            self.state.tool_records.append(record)
            self.tracer.tool_call(record)
            terminal = self._interpret(record, step, assembler)

        if terminal is not None:
            return terminal
        if saw_clarification:
            return "awaiting_clarification" if self.state.clarifying_question else "ambiguity_named"
        return None

    def _handle_clarification(self, call: ToolCallRequest, step: int) -> None:
        """Record a clarification request. At most one per run is accepted."""
        question = str(call.arguments.get("question", "")).strip()
        record = ToolCallRecord(
            step=step,
            name=call.name,
            arguments=call.arguments,
            outcome=ToolOutcome.OK,
            attempts=1,
            attempt_outcomes=[ToolOutcome.OK],
            result=f"asked: {question}" if question else "asked nothing",
        )
        self.state.tool_records.append(record)
        self.tracer.tool_call(record)

        if self.state.clarifying_question is None and question:
            self.state.clarifying_question = question
            self._record(
                FailureClass.AMBIGUOUS_INPUT,
                RunStatus.PARTIAL,
                f"awaiting clarification: {question}",
                step=step,
            )
            return

        self.state.clarifications_suppressed += 1
        self._record(
            FailureClass.AMBIGUOUS_INPUT,
            RunStatus.PARTIAL,
            f"a further clarifying question was suppressed: {question!r}",
            step=step,
        )

    def _interpret(
        self,
        record: ToolCallRecord,
        step: int,
        assembler: ContextAssembler,
    ) -> str | None:
        """Turn one tool record into either a context entry or a stop reason."""
        if record.guardrail is not None:
            return self._note_stop(
                FailureClass.GUARDRAIL_TRIP,
                RunStatus.REFUSED,
                record.error or f"guardrail {record.guardrail} tripped",
                step=step,
                guardrail=record.guardrail,
                reason=f"guardrail:{record.guardrail}",
            )

        if record.outcome is ToolOutcome.OK:
            content = record.result or ""
            assessment = assess(content)
            if assessment.trip:
                return self._note_stop(
                    FailureClass.GUARDRAIL_TRIP,
                    RunStatus.REFUSED,
                    f"untrusted content from {record.name!r}: {assessment.reason}",
                    step=step,
                    guardrail=Guardrail.UNTRUSTED_INJECTION,
                    reason="guardrail:untrusted_injection",
                )

            if record.attempts > 1:
                # Recovered. The class is recorded; the status claim is ``ok``, because
                # the required behaviour was met.
                recovered_class = (
                    FailureClass.TOOL_MALFORMED
                    if ToolOutcome.MALFORMED in record.attempt_outcomes
                    else FailureClass.TOOL_ERROR
                )
                self._record(
                    FailureClass(recovered_class),
                    RunStatus.OK,
                    f"{record.name} recovered on attempt {record.attempts}",
                    step=step,
                )
            assembler.add_tool_result(
                name=record.name,
                envelope=wrap_untrusted(
                    record.name,
                    content,
                    max_chars=self.config.guardrails.untrusted_max_chars,
                ),
            )
            return None

        failure_class = _OUTCOME_TO_CLASS[record.outcome]
        detail = f"{record.name}: {record.error or record.outcome}"
        descriptor = self._by_name.get(record.name)
        optional = descriptor.optional if descriptor is not None else True

        if optional:
            # Continue without it. The run can still finish, so it is degraded, not partial.
            self._record(failure_class, RunStatus.DEGRADED, detail, step=step)
            assembler.add_tool_result(
                name=record.name,
                envelope=wrap_untrusted(
                    record.name,
                    f"ERROR: {record.error or record.outcome}",
                    max_chars=self.config.guardrails.untrusted_max_chars,
                ),
            )
            return None

        return self._note_stop(
            failure_class,
            RunStatus.PARTIAL,
            f"{detail} (required tool, so the run cannot complete)",
            step=step,
            reason=f"required_tool_failed:{record.name}",
        )

    # -- accounting -----------------------------------------------------------

    def _record(
        self,
        failure_class: FailureClass,
        status: RunStatus,
        detail: str,
        *,
        step: int | None = None,
        guardrail: Guardrail | None = None,
    ) -> None:
        event = FailureEvent(
            failure_class=failure_class,
            status=status,
            detail=detail,
            step=step,
            guardrail=guardrail,
        )
        self.state.failures.append(event)
        self.tracer.failure(event)

    def _note_stop(
        self,
        failure_class: FailureClass,
        status: RunStatus,
        detail: str,
        *,
        step: int | None = None,
        guardrail: Guardrail | None = None,
        reason: str | None = None,
    ) -> str:
        """Record a terminal failure and return the stop reason (``""`` if unnamed).

        This is the variant used by the paths that hand a reason back to the run loop.
        Finishing happens once, in the loop, so ``run_finished`` appears exactly once
        in a trace however the run ended.
        """
        self._record(failure_class, status, detail, step=step, guardrail=guardrail)
        if reason is not None:
            self.state.reason = reason
        return reason or ""

    def _stop(
        self,
        failure_class: FailureClass,
        status: RunStatus,
        detail: str,
        *,
        step: int | None = None,
        guardrail: Guardrail | None = None,
        reason: str | None = None,
    ) -> RunOutput:
        """Record a terminal failure and finish the run in one step."""
        return self._finish(
            reason=self._note_stop(
                failure_class,
                status,
                detail,
                step=step,
                guardrail=guardrail,
                reason=reason,
            )
            or None
        )

    def _finish(self, *, reason: str | None = None) -> RunOutput:
        state = self.state
        if reason is not None:
            state.reason = reason
        output = RunOutput.build(
            trace_id=self.tracer.trace_id,
            config_name=self.config.name,
            failures=state.failures,
            output=state.output,
            reason=state.reason,
            clarifying_question=state.clarifying_question,
            steps=self.budget.steps,
            model_calls=state.model_calls,
            prompt_hashes=list(state.prompt_hashes),
            tool_calls=list(state.tool_records),
            tokens_total=self.budget.tokens_total,
            cost_usd=self.budget.cost_usd,
            wall_clock_s=round(self.clock() - self.started, 6),
            clarifications_suppressed=state.clarifications_suppressed,
        )
        self.tracer.run_finished(output)
        return output


def _repair_note(error: str, config: Configuration) -> str:
    """What the runtime tells the model after an unusable structured answer."""
    note = f"Your previous answer could not be used: {error}. Reply with only valid JSON"
    if config.output.schema_ is not None:
        note += f" matching this schema: {json.dumps(config.output.schema_)}"
    return note + ", and nothing else."


def run(
    task: str,
    *,
    config: Configuration,
    provider: Provider,
    tools: ToolBoundary,
    tracer: TraceWriter,
    budget: Budget | None = None,
    confirmation_token: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> RunOutput:
    """Run one task to a terminal status. Never raises for a task-level problem.

    ``tracer`` is required and never optional: a run with no record is not a run this
    runtime is willing to perform. The caller closes it.
    """
    return _Orchestrator(
        task=task,
        config=config,
        provider=provider,
        tools=tools,
        tracer=tracer,
        budget=budget,
        confirmation_token=confirmation_token,
        clock=clock,
    ).run()


__all__ = ["ToolBoundary", "run"]
