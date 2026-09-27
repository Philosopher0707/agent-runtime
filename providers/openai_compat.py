"""An adapter for any OpenAI-compatible ``/chat/completions`` endpoint.

The only file that knows a vendor's wire format. It normalises what comes back into
``ModelResponse`` and raises ``ProviderError`` for anything that does not conform —
it never decides a run status, and it never retries.

Transport is injectable, so the parsing and error paths are exercised against a mock
rather than against a live API.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx

from providers.base import ProviderError
from runtime.schemas import ModelResponse, ToolCallRequest, ToolDescriptor


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        price_input_per_mtok: float = 0.0,
        price_output_per_mtok: float = 0.0,
        timeout_s: float = 60.0,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._price_in = price_input_per_mtok
        self._price_out = price_output_per_mtok
        self._client = client or httpx.Client(timeout=timeout_s, transport=transport)

    # -- protocol -------------------------------------------------------------

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: Sequence[ToolDescriptor],
    ) -> ModelResponse:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]

        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"HTTP {exc.response.status_code} from {self.base_url}: {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(f"the response was not JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise ProviderError("the response was not a JSON object")
        return self._parse(body)

    def health(self) -> bool:
        try:
            response = self._client.get(f"{self.base_url}/models", headers=self._headers())
        except httpx.HTTPError:
            return False
        return response.status_code < 500

    # -- internals ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return headers

    def _parse(self, body: dict[str, Any]) -> ModelResponse:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("the response carries no choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderError("the first choice was not an object")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderError("the first choice carries no message")

        text = message.get("content")
        if text is not None and not isinstance(text, str):
            raise ProviderError(f"content was {type(text).__name__}, expected string or null")

        calls = self._parse_tool_calls(message.get("tool_calls"))

        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise ProviderError("finish_reason was not a string")

        refusal = bool(message.get("refusal")) or finish_reason == "content_filter"
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        prompt_tokens = _as_int(usage.get("prompt_tokens"))
        completion_tokens = _as_int(usage.get("completion_tokens"))

        return ModelResponse(
            text=text,
            tool_calls=calls,
            refusal=refusal,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=(
                prompt_tokens / 1_000_000 * self._price_in
                + completion_tokens / 1_000_000 * self._price_out
            ),
        )

    def _parse_tool_calls(self, raw_calls: Any) -> list[ToolCallRequest]:
        if raw_calls is None:
            return []
        if not isinstance(raw_calls, list):
            raise ProviderError("tool_calls was not a list")

        calls: list[ToolCallRequest] = []
        for raw in raw_calls:
            function = (raw or {}).get("function") if isinstance(raw, dict) else None
            if not isinstance(function, dict):
                raise ProviderError("a tool call carries no function object")
            name = function.get("name")
            if not isinstance(name, str) or not name:
                raise ProviderError("a tool call carries no function name")
            calls.append(
                ToolCallRequest(
                    name=name, arguments=_parse_arguments(name, function.get("arguments"))
                )
            )
        return calls


def _parse_arguments(name: str, raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ProviderError(f"tool arguments for {name!r} were {type(raw).__name__}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"tool arguments for {name!r} were not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProviderError(f"tool arguments for {name!r} were not a JSON object")
    return parsed


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


__all__ = ["OpenAICompatProvider"]
