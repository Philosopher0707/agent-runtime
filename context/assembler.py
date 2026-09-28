"""Assembling and truncating context. Token accounting lives here.

Two thresholds with two different jobs:

* ``summarise_above_tokens`` (soft) — summarise tool results, **oldest first**.
* ``max_prompt_tokens`` (hard) — drop the oldest turns.

The system prompt and the task statement are never candidates for either. If those
two alone exceed the hard ceiling, the assembler raises ``ContextUnfit`` rather than
quietly sending something else: the loop reports ``context_overflow`` / ``partial``
with the numbers attached.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from providers.base import estimate_tokens
from runtime.config import ContextConfig
from runtime.schemas import ContextRecord, MessageRole, ToolCallRequest, ToolDescriptor


def _schema_tokens(schemas: Sequence[ToolDescriptor], config: ContextConfig) -> int:
    """The fixed per-request cost of the tool schemas.

    Counted separately from the messages because they are serialised as JSON and JSON
    tokenises at roughly half the characters-per-token of prose — see
    ``ContextConfig.schema_chars_per_token``.
    """
    if not schemas:
        return 0
    serialised = json.dumps([schema.model_dump(mode="json") for schema in schemas])
    return estimate_tokens(serialised, config.schema_chars_per_token)


class ContextUnfit(Exception):
    """What must be kept does not fit the hard ceiling. Not recoverable here.

    Two things are kept regardless of the ceiling: the protected pair (the system prompt and
    the task), and the most recent request with the results it produced. If either cannot fit,
    the assembler refuses rather than sending a transcript that cannot be made sense of.
    """

    def __init__(self, *, estimated_tokens: int, max_prompt_tokens: int) -> None:
        super().__init__(
            f"what must be kept needs ~{estimated_tokens} tokens, ceiling is {max_prompt_tokens}"
        )
        self.estimated_tokens = estimated_tokens
        self.max_prompt_tokens = max_prompt_tokens


def summarise(text: str, *, max_chars: int) -> str:
    """Collapse a tool result to one line. Deterministic, so replay matches."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return f"[summarised] {collapsed}"
    return f"[summarised] {collapsed[:max_chars]}..."


@dataclass
class Turn:
    """One transcript entry. Mutable only via ``replace`` inside ``build``."""

    role: MessageRole
    content: str
    tool_name: str | None = None
    summarised: bool = False

    def to_message(self) -> dict[str, Any]:
        return {"role": str(self.role), "content": self.content}


@dataclass
class AssembledContext:
    messages: list[dict[str, Any]]
    record: ContextRecord


class ContextAssembler:
    """Builds the prompt. Never drops the system prompt or the task.

    ``tool_schemas`` are counted even though they are not *messages*: they are sent on
    every request as the ``tools`` field, and the provider bills them as prompt tokens.
    Leaving them out under-counted the fixed per-request overhead by about 600 tokens on
    a real endpoint — enough that the summarise and drop thresholds were operating on a
    number roughly ten times too small, so context would overflow before the runtime
    noticed it was close.
    """

    def __init__(
        self,
        *,
        system_prompt: str,
        task: str,
        config: ContextConfig,
        tool_schemas: Sequence[ToolDescriptor] = (),
    ) -> None:
        self._system_prompt = system_prompt
        self._task = task
        self._config = config
        self._turns: list[Turn] = []
        self._overhead_tokens = _schema_tokens(tool_schemas, config)

    @property
    def turn_count(self) -> int:
        return len(self._turns)

    def add_assistant(
        self, text: str | None, *, tool_calls: Sequence[ToolCallRequest] = ()
    ) -> None:
        """Record the model's turn, with any tool calls rendered in-band as text."""
        parts: list[str] = []
        if text:
            parts.append(text)
        for call in tool_calls:
            parts.append(render_tool_call(call.name, call.arguments))
        self._turns.append(Turn(role=MessageRole.ASSISTANT, content="\n".join(parts)))

    def add_tool_result(self, *, name: str, envelope: str) -> None:
        """Record a tool result. ``envelope`` must already be wrapped.

        The role is ``user`` on purpose — see ``runtime.status.MessageRole``.
        """
        self._turns.append(Turn(role=MessageRole.USER, content=envelope, tool_name=name))

    def add_runtime_note(self, note: str) -> None:
        """Record a message from the runtime itself.

        Trusted, so it is *not* wrapped in the untrusted envelope. Used for the
        structured-output repair pass, where the runtime tells the model what was
        wrong with its last answer.
        """
        self._turns.append(Turn(role=MessageRole.USER, content=note))

    def build(self, *, step: int) -> AssembledContext:
        turns = [replace(turn) for turn in self._turns]
        chars_per_token = self._config.chars_per_token
        soft = self._config.summarise_above_tokens
        hard = self._config.max_prompt_tokens

        # Two measurements, because the two thresholds ask different questions.
        #
        # `variable` is the transcript, which is the part that *grows*. The soft threshold
        # compares against this: summarising because the fixed overhead is large would
        # mean collapsing a one-token tool result to make room for a schema that never
        # changes.
        #
        # `total` is the whole request — overhead included — which is the part the provider
        # actually bills and the model actually has to fit. The hard ceiling compares
        # against this, because that is the number that overflows a context window.
        protected = (
            self._overhead_tokens
            + estimate_tokens(self._system_prompt, chars_per_token)
            + estimate_tokens(self._task, chars_per_token)
        )

        def variable() -> int:
            return sum(estimate_tokens(turn.content, chars_per_token) for turn in turns)

        def total() -> int:
            return protected + variable()

        # 1. Soft threshold: summarise tool results, oldest first.
        summarised = 0
        while variable() > soft:
            index = _first_unsummarised_tool(turns)
            if index is None:
                break
            turns[index] = replace(
                turns[index],
                content=summarise(turns[index].content, max_chars=self._config.summary_chars),
                summarised=True,
            )
            summarised += 1

        # 2. Hard ceiling: drop the oldest *group* — a request and the results it produced —
        # but never the last one.
        #
        # Two things were wrong with dropping single turns. It left the transcript incoherent:
        # the model received sixteen tool results and none of its own requests, so the prompt
        # began with an answer to a question that was no longer there. And the turns it was
        # willing to drop included the *current* request, which is the one thing the model is
        # mid-conversation with.
        #
        # Pairing alone was worse: with one 65,000-token group, dropping it emptied the
        # transcript entirely — measured, not guessed. So the last group is kept, and if even
        # that cannot fit, the assembler refuses rather than sending something misleading.
        # That is the same principle as the protected pair, applied to the other end.
        dropped = 0
        while total() > hard and _request_count(turns) > 1:
            turns.pop(0)
            dropped += 1
            while turns and turns[0].tool_name is not None:
                turns.pop(0)
                dropped += 1

        # 3. If what must be kept does not fit, refuse to pretend.
        #
        # Covers both ends: the protected pair alone (the system prompt and the task), and the
        # most recent request with its results. Sending a transcript that cannot be made sense
        # of is worse than reporting that it did not fit.
        if total() > hard:
            raise ContextUnfit(estimated_tokens=total(), max_prompt_tokens=hard)

        messages: list[dict[str, Any]] = [
            {"role": str(MessageRole.SYSTEM), "content": self._system_prompt},
            {"role": str(MessageRole.USER), "content": self._task},
            *[turn.to_message() for turn in turns],
        ]

        # The invariant this module exists to hold. Cheap, and it fails loudly.
        if not _roles_present(messages):
            raise AssertionError("context assembly dropped the system prompt or the task")

        return AssembledContext(
            messages=messages,
            record=ContextRecord(
                step=step,
                message_count=len(messages),
                estimated_tokens=total(),
                summarised_results=summarised,
                dropped_messages=dropped,
                system_prompt_present=True,
                task_present=True,
            ),
        )


def _request_count(turns: list[Turn]) -> int:
    """How many turns could be a request — anything that is not a tool result.

    Used to keep the most recent one when the hard ceiling forces drops. A tool result on its
    own is half a conversation: it is an answer, and the question it answers has to stay.
    """
    return sum(1 for turn in turns if turn.tool_name is None)


def _first_unsummarised_tool(turns: list[Turn]) -> int | None:
    for index, turn in enumerate(turns):
        if turn.tool_name is not None and not turn.summarised:
            return index
    return None


def _roles_present(messages: list[dict[str, Any]]) -> bool:
    roles = [message["role"] for message in messages]
    return roles[:2] == [str(MessageRole.SYSTEM), str(MessageRole.USER)]


def render_tool_call(name: str, arguments: dict[str, Any]) -> str:
    """The in-band rendering of a tool call the model made, for the transcript."""
    rendered_args = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
    return f"[called {name}({rendered_args})]"


__all__ = [
    "AssembledContext",
    "ContextAssembler",
    "ContextUnfit",
    "Turn",
    "render_tool_call",
    "summarise",
]
