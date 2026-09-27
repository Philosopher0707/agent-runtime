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

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from providers.base import estimate_tokens
from runtime.config import ContextConfig
from runtime.schemas import ContextRecord, MessageRole, ToolCallRequest


class ContextUnfit(Exception):
    """The protected pair does not fit the hard ceiling. Not recoverable here."""

    def __init__(self, *, estimated_tokens: int, max_prompt_tokens: int) -> None:
        super().__init__(
            f"system prompt + task need ~{estimated_tokens} tokens, ceiling is {max_prompt_tokens}"
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
    """Builds the prompt. Never drops the system prompt or the task."""

    def __init__(
        self,
        *,
        system_prompt: str,
        task: str,
        config: ContextConfig,
    ) -> None:
        self._system_prompt = system_prompt
        self._task = task
        self._config = config
        self._turns: list[Turn] = []

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

        def estimated() -> int:
            return (
                estimate_tokens(self._system_prompt, chars_per_token)
                + estimate_tokens(self._task, chars_per_token)
                + sum(estimate_tokens(turn.content, chars_per_token) for turn in turns)
            )

        # 1. Soft threshold: summarise tool results, oldest first.
        summarised = 0
        while estimated() > soft:
            index = _first_unsummarised_tool(turns)
            if index is None:
                break
            turns[index] = replace(
                turns[index],
                content=summarise(turns[index].content, max_chars=self._config.summary_chars),
                summarised=True,
            )
            summarised += 1

        # 2. Hard ceiling: drop the oldest turns. Never the protected pair.
        dropped = 0
        while estimated() > hard and turns:
            turns.pop(0)
            dropped += 1

        # 3. If the protected pair alone does not fit, refuse to pretend.
        protected = estimate_tokens(self._system_prompt, chars_per_token) + estimate_tokens(
            self._task, chars_per_token
        )
        if protected > hard:
            raise ContextUnfit(estimated_tokens=protected, max_prompt_tokens=hard)

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
                estimated_tokens=estimated(),
                summarised_results=summarised,
                dropped_messages=dropped,
                system_prompt_present=True,
                task_present=True,
            ),
        )


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
