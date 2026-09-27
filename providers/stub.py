"""The stub provider: a deterministic model, scripted by a test or an eval case.

It exists so the loop, the budget, and the taxonomy can be tested against a real
contract instead of a mock. Usage numbers are derived from content, so a run and its
replay produce identical accounting.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from providers.base import ProviderError, estimate_tokens, messages_token_estimate
from runtime.schemas import ModelResponse, ToolDescriptor


class StubProvider:
    """Replays a fixed script of model turns, then answers with ``default_final``."""

    name = "stub"

    def __init__(
        self,
        *,
        model: str = "stub-mid-tier",
        script: Iterable[ModelResponse | dict[str, Any]] = (),
        default_final: str = "Done.",
        price_input_per_mtok: float = 0.0,
        price_output_per_mtok: float = 0.0,
        chars_per_token: float = 4.0,
        fail_on_call: int | None = None,
        fail_message: str = "simulated provider failure",
    ) -> None:
        self.model = model
        self._script: list[ModelResponse] = [
            item if isinstance(item, ModelResponse) else ModelResponse.model_validate(item)
            for item in script
        ]
        self._default_final = default_final
        self._price_in = price_input_per_mtok
        self._price_out = price_output_per_mtok
        self._chars_per_token = chars_per_token
        self._fail_on_call = fail_on_call
        self._fail_message = fail_message
        self._index = 0
        self.calls: list[list[dict[str, Any]]] = []

    @property
    def calls_made(self) -> int:
        return len(self.calls)

    @property
    def script_exhausted(self) -> bool:
        return self._index >= len(self._script)

    def health(self) -> bool:
        return True

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: Sequence[ToolDescriptor],
    ) -> ModelResponse:
        if self._fail_on_call is not None and len(self.calls) + 1 == self._fail_on_call:
            raise ProviderError(self._fail_message)
        self.calls.append([dict(message) for message in messages])

        if self._index < len(self._script):
            response = self._script[self._index]
            self._index += 1
        else:
            response = ModelResponse(text=self._default_final, finish_reason="stop")

        prompt_tokens = messages_token_estimate(messages, self._chars_per_token) + sum(
            estimate_tokens(json.dumps(tool.model_dump(mode="json")), self._chars_per_token)
            for tool in tools
        )
        completion_tokens = estimate_tokens(response.text or "", self._chars_per_token) + sum(
            estimate_tokens(json.dumps(call.model_dump(mode="json")), self._chars_per_token)
            for call in response.tool_calls
        )
        cost = (
            prompt_tokens / 1_000_000 * self._price_in
            + completion_tokens / 1_000_000 * self._price_out
        )
        return response.model_copy(
            update={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": cost,
                "finish_reason": response.finish_reason or _default_finish_reason(response),
            }
        )


def _default_finish_reason(response: ModelResponse) -> str:
    if response.refusal:
        return "refusal"
    if response.tool_calls:
        return "tool_calls"
    return "stop"


def text_response(text: str) -> ModelResponse:
    """Convenience for building a script entry that just answers."""
    return ModelResponse(text=text, finish_reason="stop")


def tool_call_response(name: str, arguments: dict[str, Any] | None = None) -> ModelResponse:
    """Convenience for building a script entry that calls one tool."""
    from runtime.schemas import ToolCallRequest

    return ModelResponse(
        tool_calls=[ToolCallRequest(name=name, arguments=arguments or {})],
        finish_reason="tool_calls",
    )


__all__ = ["StubProvider", "text_response", "tool_call_response"]
