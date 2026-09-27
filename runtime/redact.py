"""Redaction for the durable record.

The spec draws a line between two different things, and they are genuinely different:

* **What may enter context.** Everything the principal sends, and everything a tool
  returns. It is **not** redacted. Redacting the prompt would silently change the task,
  and a runtime that quietly answers a different question than the one asked is worse
  than one that declines. The controls on what enters context are the *tool set* a
  configuration chooses and the input-size guardrail — not a text filter.
* **What must be redacted before logging.** The trace, because it is the durable
  artefact: it outlives the prompt, it is read by people who were not party to the run,
  and it is the thing that gets copied into a bug report.

Redaction applies at one chokepoint — :meth:`runtime.trace.TraceWriter.emit` — so no
event kind can bypass it by being added later.

**Redaction and replay are mutually exclusive, and that is the point of this module.**
Replay rebuilds each prompt from the trace and compares its hash against the recorded
one. A redacted trace cannot reproduce the prompt it recorded, so a redacted trace is not
replayable, and :func:`runtime.replay.replay` refuses one rather than reporting a
divergence that is really a policy. Turning redaction on is therefore a deliberate trade:
durability of the record, or the ability to replay it. You do not get both, and pretending
otherwise would mean one of the two silently not working.

Note the failure economics, which are the **opposite** of the injection guardrail's: a
false positive here loses a digit from a log, while a false negative leaks personal data.
So the patterns are deliberately aggressive, and over-matching is the acceptable error.
The injection markers must be precise because a false positive refuses a run; these must
not be, because a false negative is the harm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Prefix on every replacement, so a redacted value is obvious in a trace and greppable.
MARKER = "[redacted"


@dataclass(frozen=True)
class RedactionPattern:
    name: str
    pattern: re.Pattern[str]
    describes: str


PATTERNS: tuple[RedactionPattern, ...] = (
    RedactionPattern(
        name="email",
        describes="An email address.",
        pattern=re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    ),
    RedactionPattern(
        name="phone",
        describes="A phone number, in an international, parenthesised or 3-3-4 shape.",
        # Deliberately shaped to avoid matching an ISO date: every alternative needs a
        # leading '+', parentheses, or 3-3-4 grouping. "2026-09-27" matches none of them.
        pattern=re.compile(
            r"\+\d[\d ().-]{6,}\d"
            r"|\(\d{2,4}\)[\s.-]?\d{2,4}[\s.-]?\d{2,4}"
            r"|\b\d{3}[\s.-]\d{3}[\s.-]\d{4}\b"
        ),
    ),
    RedactionPattern(
        name="credit_card",
        describes="A payment card number, grouped or unbroken.",
        pattern=re.compile(r"\b(?:\d{4}[ -]?){3}\d{1,4}\b"),
    ),
    RedactionPattern(
        name="national_id",
        describes="A US SSN-shaped national identifier.",
        pattern=re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    RedactionPattern(
        name="api_key",
        describes="A credential with a recognisable vendor prefix.",
        pattern=re.compile(r"\b(?:sk|ghp|gho|ghs|ghu|xox[baprs])[_-]?[A-Za-z0-9_-]{16,}\b"),
    ),
)

PATTERN_NAMES: frozenset[str] = frozenset(pattern.name for pattern in PATTERNS)

#: The set a configuration gets when it turns redaction on without naming patterns.
DEFAULT_PATTERNS: tuple[str, ...] = tuple(pattern.name for pattern in PATTERNS)


class RedactionError(ValueError):
    """A redaction configuration that cannot be honoured."""


@dataclass
class RedactionCounts:
    """How many values each pattern replaced. Recorded so redaction is visible."""

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, name: str, amount: int) -> None:
        if amount:
            self.counts[name] = self.counts.get(name, 0) + amount

    def merge(self, other: RedactionCounts) -> None:
        for name, amount in other.counts.items():
            self.add(name, amount)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def as_dict(self) -> dict[str, Any]:
        return {"total": self.total, "by_pattern": dict(sorted(self.counts.items()))}


class Redactor:
    """Applies a set of patterns to text, and to strings nested in a payload."""

    def __init__(self, patterns: tuple[RedactionPattern, ...]) -> None:
        if not patterns:
            raise RedactionError("a redactor needs at least one pattern")
        self.patterns = patterns

    @classmethod
    def from_names(cls, names: list[str] | tuple[str, ...]) -> Redactor:
        unknown = sorted(set(names) - PATTERN_NAMES)
        if unknown:
            raise RedactionError(
                f"unknown redaction pattern(s): {unknown}. Known: {sorted(PATTERN_NAMES)}"
            )
        selected = tuple(pattern for pattern in PATTERNS if pattern.name in set(names))
        return cls(selected)

    def text(self, value: str) -> tuple[str, RedactionCounts]:
        counts = RedactionCounts()
        for pattern in self.patterns:
            value, replaced = pattern.pattern.subn(f"{MARKER}:{pattern.name}]", value)
            counts.add(pattern.name, replaced)
        return value, counts

    def value(self, payload: Any) -> tuple[Any, RedactionCounts]:
        """Redact every string inside a JSON-shaped payload, keys included.

        Keys are redacted too. A tool that returns a mapping keyed by an email address is
        not a hypothetical, and a redactor that only walked values would leak it.
        """
        counts = RedactionCounts()

        def walk(node: Any) -> Any:
            if isinstance(node, str):
                redacted, seen = self.text(node)
                counts.merge(seen)
                return redacted
            if isinstance(node, dict):
                return {walk(key): walk(item) for key, item in node.items()}
            if isinstance(node, (list, tuple)):
                return [walk(item) for item in node]
            return node

        return walk(payload), counts


__all__ = [
    "DEFAULT_PATTERNS",
    "MARKER",
    "PATTERNS",
    "PATTERN_NAMES",
    "RedactionCounts",
    "RedactionError",
    "RedactionPattern",
    "Redactor",
]
