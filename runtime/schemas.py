"""Every schema that crosses a boundary.

No bare dicts cross a boundary in this runtime. If data moves between two
components, or between the runtime and the outside world, it is one of these.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from runtime.status import (
    FailureClass,
    Guardrail,
    MessageRole,
    RunStatus,
    ToolOutcome,
    dedupe,
    resolve_status,
)


class Contract(BaseModel):
    """Base for all boundary schemas.

    ``extra="forbid"`` is deliberate: a payload that carries an unexpected field
    is a contract violation, not a payload to be quietly tolerated.
    """

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- model


class ToolCallRequest(Contract):
    """A model's request to call a tool. Untrusted until validated."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelResponse(Contract):
    """A normalised model turn, whatever the provider's wire format was."""

    text: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    refusal: bool = False
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


# --------------------------------------------------------------------------- tools


class ToolDescriptor(Contract):
    """What the model is told about a tool. Declared, never inferred."""

    name: str
    description: str
    parameters: dict[str, Any]
    side_effect: bool
    idempotent: bool
    optional: bool


class Spend(Contract):
    """What one call consumed, in the two units a budget charges for.

    **Not a `Budget`.** A budget is a *bound* — four limits and a clock — and a call does not
    have one. This is the usage side of the same accounting: the number a tool reports and a
    record carries. Only the two units a call can *cause*: tokens and money. Steps and
    wall-clock belong to the run that is spending, not to the call.

    It exists because of a defect rather than a design instinct. A delegated run's cost was
    charged to its parent from inside the code that starts the child, so the charge happened
    only when a child really ran — and replay, which serves a spawn from the record and never
    starts a child, reported a parent cost of `$0.0073` against a recorded `$0.0174`. The
    number has to travel *in the record*, or replay cannot restore it
    ([decisions/0031](../docs/decisions/0031-the-spend-is-in-the-record.md)).

    Frozen, because a spend that can be edited after the fact is not a record.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tokens_total: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)

    def __add__(self, other: Spend) -> Spend:
        """Attempts add up. A call that failed and then succeeded was paid for twice."""
        return Spend(
            tokens_total=self.tokens_total + other.tokens_total,
            cost_usd=self.cost_usd + other.cost_usd,
        )

    @property
    def is_zero(self) -> bool:
        """True for a call that cost nothing, which is every call that does not delegate."""
        return self.tokens_total == 0 and self.cost_usd == 0.0


@dataclass(frozen=True)
class ToolResult:
    """What a tool returns when it has more to report than text.

    A tool's result is text, and almost always that is all of it. But a tool that *spends* —
    starts a run, calls a paid service — has one more fact to report, and the registry has no
    other channel: `invoke` returns, or it raises, and that is the whole of it.

    So the channel carries two shapes. A bare `str` means "text, and I spent nothing", which is
    what every existing tool returns and most will keep returning. This type means "text, and
    here is what it cost". It is not optional for a tool that spends, because a spend nobody
    reports is a bound that cannot bind.

    A tool that fails *after* spending reports it on the exception instead — see
    `tools.registry.ToolError`. A failed delegation is not a free one.
    """

    text: str
    spend: Spend = field(default_factory=Spend)


class ToolCallRecord(Contract):
    """The full account of one tool invocation, attempts included.

    ``attempt_outcomes`` records every attempt in order; ``outcome`` is the last
    one. Replay reads this sequence, which is why a retried call replays exactly
    rather than being re-decided.
    """

    step: int
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    outcome: ToolOutcome
    attempts: int = 1
    attempt_outcomes: list[ToolOutcome] = Field(default_factory=list)
    duration_s: float = 0.0
    #: What this call consumed. Zero for almost every tool — a read is free. Non-zero for a
    #: tool that spends, which today means a delegated run.
    #:
    #: **In the record rather than in the tool, because replay never runs the tool.** The loop
    #: charges this on both paths, so a replayed run reports the same cost as the run that
    #: produced it. An older trace has no spend recorded, which is true of it: it was not
    #: recorded, so its replay understates exactly as it did before. That is why this field is
    #: optional and the schema version did not change.
    spend: Spend = Field(default_factory=Spend)
    result: str | None = None
    error: str | None = None
    guardrail: Guardrail | None = None
    #: True when the runtime injected the principal's confirmation token.
    #: ``arguments`` holds what the *model* asked for; this records what the runtime
    #: actually authorised. The two are deliberately kept separate.
    confirmation_applied: bool = False

    @model_validator(mode="after")
    def _outcome_matches_last_attempt(self) -> ToolCallRecord:
        if self.attempt_outcomes and self.attempt_outcomes[-1] is not self.outcome:
            raise ValueError(
                f"outcome {self.outcome!r} must equal the last attempt "
                f"{self.attempt_outcomes[-1]!r}"
            )
        return self


# -------------------------------------------------------------------------- model


class ModelCallRecord(Contract):
    """One model call, and the response that must be replayed verbatim.

    ``response`` is carried in full on purpose: a trace alone has to be enough to
    reconstruct the run, and the response is the only thing the loop cannot
    recompute.
    """

    step: int
    provider: str
    model: str
    prompt_hash: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    cost_usd: float
    finish_reason: str | None = None
    refusal: bool = False
    response: ModelResponse


# ------------------------------------------------------------------------ context


class ContextRecord(Contract):
    """Per-step evidence that the context invariants held.

    Recorded so the claim "the system prompt is never dropped" is checkable from
    a trace rather than asserted in prose.
    """

    step: int
    message_count: int
    estimated_tokens: int
    summarised_results: int
    dropped_messages: int
    system_prompt_present: bool
    task_present: bool


# ----------------------------------------------------------------------- failures


class FailureEvent(Contract):
    """One observed failure, with the status claim it makes."""

    failure_class: FailureClass
    status: RunStatus
    detail: str
    step: int | None = None
    guardrail: Guardrail | None = None


# --------------------------------------------------------------------------- runs


class RunRequest(Contract):
    """Input to the runtime.

    ``confirmation_token`` is the principal's authorisation for side-effecting
    tools. It is supplied by the caller and injected by the tool boundary; the
    model never sees it and never gets to choose it.
    """

    task: str
    config: str = "default"
    trace_id: str | None = None
    confirmation_token: str | None = None


class RunOutput(Contract):
    """The runtime's answer about a run. Also the ``POST /run`` response body."""

    trace_id: str
    config_name: str
    status: RunStatus
    output: str | None = None
    reason: str | None = None
    clarifying_question: str | None = None
    failure_classes: list[FailureClass] = Field(default_factory=list)
    failures: list[FailureEvent] = Field(default_factory=list)
    steps: int = 0
    model_calls: int = 0
    #: One hash per model call, in order. Carried in the output so that "this run is
    #: reproducible" is checkable by a caller, not just asserted by us.
    prompt_hashes: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    tokens_total: int = 0
    cost_usd: float = 0.0
    wall_clock_s: float = 0.0
    clarifications_suppressed: int = 0
    scorer: str = "deterministic"

    @classmethod
    def build(
        cls,
        *,
        trace_id: str,
        config_name: str,
        failures: list[FailureEvent],
        **rest: Any,
    ) -> RunOutput:
        """Assemble the output, deriving status from the failure claims."""
        return cls(
            trace_id=trace_id,
            config_name=config_name,
            status=resolve_status(event.status for event in failures),
            failure_classes=dedupe(event.failure_class for event in failures),
            failures=failures,
            **rest,
        )

    def canonical(self) -> dict[str, Any]:
        """Deterministic projection, for comparing a run against its replay.

        Excludes everything that is legitimately wall-clock dependent: the trace
        id, latencies, durations, and total elapsed time. Everything else is
        included — notably ``prompt_hashes``, so a replay only compares equal if
        it rebuilt the byte-identical prompts at every step.
        """
        return {
            "config_name": self.config_name,
            "status": str(self.status),
            "output": self.output,
            "reason": self.reason,
            "clarifying_question": self.clarifying_question,
            "failure_classes": [str(c) for c in self.failure_classes],
            "steps": self.steps,
            "model_calls": self.model_calls,
            "prompt_hashes": list(self.prompt_hashes),
            "tokens_total": self.tokens_total,
            "cost_usd": round(self.cost_usd, 9),
            "clarifications_suppressed": self.clarifications_suppressed,
            "tool_calls": [
                {
                    "step": rec.step,
                    "name": rec.name,
                    "arguments": rec.arguments,
                    "outcome": str(rec.outcome),
                    "attempts": rec.attempts,
                    "attempt_outcomes": [str(o) for o in rec.attempt_outcomes],
                    # Rounded like the top-level cost, so a replay comparison is not decided
                    # by float noise in a number that was summed in a different order.
                    "spend": {
                        "tokens_total": rec.spend.tokens_total,
                        "cost_usd": round(rec.spend.cost_usd, 9),
                    },
                    "result": rec.result,
                    "error": rec.error,
                    "guardrail": str(rec.guardrail) if rec.guardrail else None,
                    "confirmation_applied": rec.confirmation_applied,
                }
                for rec in self.tool_calls
            ],
        }


# ------------------------------------------------------------------------ service


class HealthResponse(Contract):
    status: Literal["ok", "degraded"]
    version: str
    uptime_s: float
    model_reachable: bool
    tools_loaded: int


# --------------------------------------------------------------------------- trace


#: The format every line of a trace is written in.
#:
#: Bumped when the *meaning* of a payload changes in a way replay cannot absorb — a
#: renamed field, a changed shape, a removed event. Adding a new optional event does not
#: need a bump; changing what an existing one means does.
#:
#: Version 1 is the format as it exists at the moment this field was introduced, so every
#: trace written before it is genuinely version 1 rather than unknown. That is why the
#: field has a default instead of being required.
TRACE_SCHEMA_VERSION = 1


class TraceEvent(Contract):
    """One line of a trace. Append-only; never rewritten."""

    #: Carried on every line rather than in a header, so a file written by an older build
    #: is detected per line and a mixed file cannot pass as a whole one.
    schema_version: int = TRACE_SCHEMA_VERSION
    ts: str
    trace_id: str
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)


class TraceRecord(Contract):
    """A whole trace, parsed. The only input replay needs."""

    trace_id: str
    events: list[TraceEvent]

    def of(self, event: str) -> list[TraceEvent]:
        return [e for e in self.events if e.event == event]

    def first(self, event: str) -> TraceEvent | None:
        matches = self.of(event)
        return matches[0] if matches else None


@dataclass(frozen=True)
class SpawnRequest:
    """A request to start a run from inside a run.

    **The shape lives in the runtime, not in the tool that asks for it.** Starting a bounded,
    traced, replayable run *is* what this runtime does; deciding *when* to is the agent's. So
    the mechanism is a primitive here and the judgement is a capability in `tools/`, which is
    what keeps the core from importing a tool and the delete test passing.
    """

    task: str
    #: Which configuration the child runs. ``None`` means "the same shape as the parent".
    config: str | None
    #: The fraction of the parent's *remaining* budget the child may use.
    share: float
    #: Whether the child may use the caller's confirmation token. Off unless a configuration
    #: says otherwise — see decisions/0028.
    inherit_confirmation: bool


@dataclass(frozen=True)
class SpawnResult:
    """What a spawned run reported back."""

    trace_id: str
    status: str
    output: str | None
    reason: str | None
    steps: int
    #: What the child consumed. Carried as a `Spend` rather than as two loose numbers because
    #: this is the value that has to survive into the parent's tool record — and then into the
    #: trace — for a replay to charge the same amount. One shape, three hops, no re-assembly.
    spend: Spend

    def render(self) -> str:
        """How the child's outcome reaches the parent's transcript.

        The trace id is in the *header*, not buried, because the child's trace is the only place
        its reasoning lives. This string is also the linkage between the two traces: the
        parent's `tool_call` record carries it, which is what makes each findable from the other.
        """
        header = (
            f"[sub-agent] status={self.status} steps={self.steps} "
            f"cost=${self.spend.cost_usd:.4f} trace={self.trace_id}"
        )
        if self.reason:
            header += f" reason={self.reason}"
        body = self.output if self.output is not None else "(no answer)"
        return f"{header}\n{body}"


#: How a run is started from inside a run. Injected by the composition root, never imported by
#: a tool — a tool that imported the loop would invert the layering that keeps the core general.
SpawnRunner = Callable[["SpawnRequest"], "SpawnResult"]


__all__ = [
    "ContextRecord",
    "Contract",
    "FailureEvent",
    "HealthResponse",
    "MessageRole",
    "ModelCallRecord",
    "ModelResponse",
    "RunOutput",
    "RunRequest",
    "SpawnRequest",
    "SpawnResult",
    "SpawnRunner",
    "Spend",
    "ToolCallRecord",
    "ToolCallRequest",
    "ToolDescriptor",
    "ToolResult",
    "TraceEvent",
    "TraceRecord",
]
