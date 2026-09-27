"""The provider boundary: the only code that talks to a model API.

The loop imports this module for the ``Provider`` protocol and the prompt hash, and
nothing else. Concrete adapters (``providers.stub``, ``providers.openai_compat``,
``providers.replay``) are constructed at the composition root and handed to the
loop as a ``Provider`` — the loop never names a vendor.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol, runtime_checkable

from runtime.schemas import ModelResponse, ToolDescriptor


class ProviderError(Exception):
    """A model call could not be completed or returned a non-conforming payload.

    Adapters raise this; the loop turns it into ``failure_class=provider_error``,
    ``status=failed``. Adapters do not decide run status.
    """


@runtime_checkable
class Provider(Protocol):
    """What the loop needs from a model. Deliberately three things."""

    name: str
    model: str

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDescriptor],
    ) -> ModelResponse:
        """Return one normalised model turn, or raise ``ProviderError``."""
        ...

    def health(self) -> bool:
        """Cheap reachability probe for ``GET /healthz``. Never raises."""
        ...


def prompt_hash(messages: list[dict[str, Any]], tools: list[ToolDescriptor]) -> str:
    """Stable hash of exactly what was sent to the model.

    Canonical JSON, keys sorted, so the hash depends on content and not on
    dictionary insertion order. Replay recomputes this and compares, which is what
    makes "the replay rebuilt the same prompt" a checked fact rather than a hope.
    """
    payload = {
        "messages": messages,
        "tools": sorted(
            (tool.model_dump(mode="json") for tool in tools),
            key=lambda t: t["name"],
        ),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def estimate_tokens(text: str, chars_per_token: float = 4.0) -> int:
    """Heuristic token estimate.

    A real tokeniser per provider would be more accurate and is the documented
    upgrade path (docs/decisions/0005). A heuristic is chosen here because the
    budget must be enforced identically for every provider, including ones whose
    tokeniser we do not have.
    """
    if not text:
        return 0
    return max(1, int(len(text) / max(chars_per_token, 0.001)))


def messages_token_estimate(messages: list[dict[str, Any]], chars_per_token: float = 4.0) -> int:
    return sum(
        estimate_tokens(str(message.get("content") or ""), chars_per_token) for message in messages
    )


__all__ = [
    "Provider",
    "ProviderError",
    "estimate_tokens",
    "messages_token_estimate",
    "prompt_hash",
]
