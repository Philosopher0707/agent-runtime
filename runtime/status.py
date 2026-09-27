"""The vocabulary. One place decides what a run can say about itself.

Every component that wants to report an outcome speaks these words and no others.
If a new word is needed, it is added here, in a commit that says why.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class RunStatus(StrEnum):
    """Terminal status of a run. Exactly one per run."""

    OK = "ok"
    DEGRADED = "degraded"
    PARTIAL = "partial"
    FAILED = "failed"
    REFUSED = "refused"


class FailureClass(StrEnum):
    """The failure taxonomy. One class per way a run can go wrong.

    Classes are *recorded*; statuses are *derived*. A class may be recorded while
    the run still ends ``ok`` — a tool that errored and then succeeded on retry is
    a recorded ``TOOL_ERROR`` with no status claim above ``ok``.
    """

    NONE = "none"
    TOOL_ERROR = "tool_error"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_MALFORMED = "tool_malformed"
    MODEL_REFUSAL = "model_refusal"
    CONTEXT_OVERFLOW = "context_overflow"
    AMBIGUOUS_INPUT = "ambiguous_input"
    BUDGET_EXHAUSTED = "budget_exhausted"
    GUARDRAIL_TRIP = "guardrail_trip"
    UNPARSEABLE_OUTPUT = "unparseable_output"
    # Added beyond the initial taxonomy: a provider that cannot be reached or that
    # returns a non-conforming payload has no home among the rows above. See
    # docs/decisions/0004-failure-taxonomy-provider-error.md.
    PROVIDER_ERROR = "provider_error"


class ToolOutcome(StrEnum):
    """How a single tool invocation ended."""

    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"
    NOT_EXECUTED = "not_executed"


class Guardrail(StrEnum):
    """Named guardrails. A trip reports *which* one, never just "a guardrail"."""

    EMPTY_INPUT = "empty_input"
    INPUT_TOO_LARGE = "input_too_large"
    UNTRUSTED_INJECTION = "untrusted_injection"
    CONFIRMATION_MISSING = "confirmation_missing"
    SYSTEM_PROMPT_DISCLOSURE = "system_prompt_disclosure"


class MessageRole(StrEnum):
    """Roles in the transcript.

    There is no ``tool`` role. A tool result travels as a ``user`` message wrapped
    in an explicit untrusted envelope (``context.sanitize``), which keeps the
    protocol provider-agnostic: it needs no vendor-specific tool-call ids, and it
    works against a model that has no native tool-calling at all.
    """

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


#: Severity order for deriving one status from many claims. Highest wins.
#: Rationale in docs/decisions/0002-status-precedence.md.
_SEVERITY: dict[RunStatus, int] = {
    RunStatus.OK: 0,
    RunStatus.DEGRADED: 1,
    RunStatus.PARTIAL: 2,
    RunStatus.FAILED: 3,
    RunStatus.REFUSED: 4,
}


def severity(status: RunStatus) -> int:
    return _SEVERITY[status]


def resolve_status(claims: Iterable[RunStatus]) -> RunStatus:
    """Collapse many status claims into the one status the run reports.

    A refusal outranks a failure outranks a partial outranks a degradation. The
    most specific claim about the run wins; nothing is hidden, because every
    individual claim survives in ``RunOutput.failures``.
    """
    worst = RunStatus.OK
    for claim in claims:
        if _SEVERITY[claim] > _SEVERITY[worst]:
            worst = claim
    return worst


def dedupe(classes: Iterable[FailureClass]) -> list[FailureClass]:
    """Ordered dedupe, dropping NONE. Preserves first-observed order."""
    seen: dict[FailureClass, None] = {}
    for cls in classes:
        if cls is not FailureClass.NONE:
            seen.setdefault(cls, None)
    return list(seen)
