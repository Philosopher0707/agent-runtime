"""The provider adapter, exercised against a mock transport.

Every parsing and error path is reachable without a network or an API key, which is the
point of injecting the transport: the only thing left unverified is the vendor's actual
behaviour, and that is stated plainly rather than assumed.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from providers.base import ProviderError
from providers.openai_compat import OpenAICompatProvider
from runtime.schemas import ToolDescriptor

Handler = Callable[[httpx.Request], httpx.Response]


def make_provider(handler: Handler, **kwargs: object) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        base_url="https://api.example.test/v1",
        model="test-model",
        transport=httpx.MockTransport(handler),
        **kwargs,  # type: ignore[arg-type]
    )


def json_response(payload: object, status: int = 200) -> Handler:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def completion(
    *,
    content: str | None = "hello",
    tool_calls: list[dict[str, object]] | None = None,
    finish_reason: str = "stop",
    usage: dict[str, int] | None = None,
    extra_message: dict[str, object] | None = None,
) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    if extra_message:
        message.update(extra_message)
    return {
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20},
    }


TOOL = ToolDescriptor(
    name="calculator",
    description="Does arithmetic.",
    parameters={"type": "object", "properties": {"expression": {"type": "string"}}},
    side_effect=False,
    idempotent=True,
    optional=True,
)


def test_a_text_completion_is_normalised() -> None:
    provider = make_provider(json_response(completion(content="hi")))
    response = provider.complete([{"role": "user", "content": "go"}], [TOOL])
    assert response.text == "hi"
    assert response.tool_calls == []
    assert (response.prompt_tokens, response.completion_tokens) == (100, 20)
    assert response.refusal is False


def test_cost_is_derived_from_the_declared_prices() -> None:
    provider = make_provider(
        json_response(completion()), price_input_per_mtok=1.0, price_output_per_mtok=2.0
    )
    response = provider.complete([{"role": "user", "content": "go"}], [])
    assert response.cost_usd == pytest.approx(100 / 1e6 * 1.0 + 20 / 1e6 * 2.0)


def test_tool_calls_with_string_arguments_are_parsed() -> None:
    payload = completion(
        content=None,
        finish_reason="tool_calls",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "calculator", "arguments": '{"expression": "1+1"}'},
            }
        ],
    )
    response = make_provider(json_response(payload)).complete([], [TOOL])
    assert response.tool_calls[0].name == "calculator"
    assert response.tool_calls[0].arguments == {"expression": "1+1"}


def test_tool_calls_with_object_arguments_are_parsed() -> None:
    payload = completion(
        content=None,
        tool_calls=[{"function": {"name": "calculator", "arguments": {"expression": "1+1"}}}],
    )
    response = make_provider(json_response(payload)).complete([], [TOOL])
    assert response.tool_calls[0].arguments == {"expression": "1+1"}


def test_empty_arguments_become_an_empty_mapping() -> None:
    payload = completion(
        content=None, tool_calls=[{"function": {"name": "clock", "arguments": ""}}]
    )
    response = make_provider(json_response(payload)).complete([], [TOOL])
    assert response.tool_calls[0].arguments == {}


def test_a_content_filter_is_a_refusal() -> None:
    payload = completion(content="", finish_reason="content_filter")
    response = make_provider(json_response(payload)).complete([], [])
    assert response.refusal is True


def test_an_explicit_refusal_field_is_a_refusal() -> None:
    payload = completion(content=None, extra_message={"refusal": "I won't."})
    response = make_provider(json_response(payload)).complete([], [])
    assert response.refusal is True


def test_a_response_with_no_choices_is_a_provider_error() -> None:
    with pytest.raises(ProviderError, match="no choices"):
        make_provider(json_response({"choices": []})).complete([], [])


def test_a_response_that_is_not_json_is_a_provider_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    with pytest.raises(ProviderError, match="not JSON"):
        make_provider(handler).complete([], [])


def test_an_http_error_is_a_provider_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream is unwell")

    with pytest.raises(ProviderError, match="HTTP 500"):
        make_provider(handler).complete([], [])


def test_a_transport_failure_is_a_provider_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ProviderError, match="ConnectError"):
        make_provider(handler).complete([], [])


def test_tool_arguments_that_are_not_json_are_a_provider_error() -> None:
    payload = completion(
        content=None,
        tool_calls=[{"function": {"name": "calculator", "arguments": "{not json"}}],
    )
    with pytest.raises(ProviderError, match="not JSON"):
        make_provider(json_response(payload)).complete([], [TOOL])


def test_a_tool_call_without_a_name_is_a_provider_error() -> None:
    payload = completion(content=None, tool_calls=[{"function": {"arguments": "{}"}}])
    with pytest.raises(ProviderError, match="no function name"):
        make_provider(json_response(payload)).complete([], [TOOL])


def test_content_that_is_not_text_is_a_provider_error() -> None:
    payload = completion(content=123)  # type: ignore[arg-type]
    with pytest.raises(ProviderError, match="content was int"):
        make_provider(json_response(payload)).complete([], [])


def test_the_request_carries_the_tools_and_the_key() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=completion())

    provider = make_provider(handler, api_key="secret-key")
    provider.complete([{"role": "user", "content": "go"}], [TOOL])

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "test-model"
    assert body["tools"][0]["function"]["name"] == "calculator"
    assert captured["auth"] == "Bearer secret-key"


def test_no_authorization_header_without_a_key() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=completion())

    make_provider(handler).complete([{"role": "user", "content": "go"}], [])
    assert captured["auth"] is None


def test_health_is_true_when_the_endpoint_answers() -> None:
    assert make_provider(json_response({"data": []})).health() is True


def test_health_is_false_when_the_transport_fails() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    assert make_provider(handler).health() is False


def test_health_is_false_on_a_server_error() -> None:
    assert make_provider(json_response({}, status=503)).health() is False


def test_usage_that_is_missing_or_nonsense_becomes_zero() -> None:
    payload = completion(usage={})
    payload["usage"] = {"prompt_tokens": "many", "completion_tokens": None}
    response = make_provider(json_response(payload)).complete([], [])
    assert (response.prompt_tokens, response.completion_tokens) == (0, 0)
