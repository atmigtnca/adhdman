"""Offline tests for the OpenRouter provider.

A fake ``httpx`` transport intercepts every request so no real network calls
are made. We assert request shape (URL, model, auth header, timeout config)
and verify error mapping for timeouts, HTTP failures, and malformed bodies.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.llm.base import LLMError, LLMResult
from app.llm.openrouter import OpenRouterProvider


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "OPENROUTER_API_KEY": "test-key",
        "OPENROUTER_BASE_URL": "https://openrouter.example/api/v1",
        "OPENROUTER_MODEL": "inclusionai/ring-2.6-1t",
        "LLM_TIMEOUT_SECONDS": 1.5,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _success_body(content: str = '{"intent":"task"}') -> bytes:
    return json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": content}}]}
    ).encode("utf-8")


def test_unavailable_when_api_key_missing() -> None:
    provider = OpenRouterProvider(_settings(OPENROUTER_API_KEY=None))

    assert provider.available is False

    result = provider.complete("sys", "hello")

    assert isinstance(result, LLMError)
    assert result.kind == "unavailable"


def test_complete_sends_expected_request_shape() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("authorization")
        captured["content_type"] = request.headers.get("content-type")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=_success_body("ok"))

    provider = OpenRouterProvider(
        _settings(), transport=httpx.MockTransport(handler)
    )

    result = provider.complete("system prompt", "user text")

    assert isinstance(result, LLMResult)
    assert result.text == "ok"
    assert captured["method"] == "POST"
    assert captured["url"] == "https://openrouter.example/api/v1/chat/completions"
    assert captured["auth"] == "Bearer test-key"
    assert "application/json" in str(captured["content_type"])

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "inclusionai/ring-2.6-1t"
    assert body["temperature"] == 0.0
    assert body["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user text"},
    ]


def test_timeout_is_mapped_to_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=request)

    provider = OpenRouterProvider(
        _settings(), transport=httpx.MockTransport(handler)
    )

    result = provider.complete("sys", "user")

    assert isinstance(result, LLMError)
    assert result.kind == "timeout"


def test_http_error_status_maps_to_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"server boom")

    provider = OpenRouterProvider(
        _settings(), transport=httpx.MockTransport(handler)
    )

    result = provider.complete("sys", "user")

    assert isinstance(result, LLMError)
    assert result.kind == "http_error"
    assert "500" in result.message


def test_invalid_response_body_maps_to_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    provider = OpenRouterProvider(
        _settings(), transport=httpx.MockTransport(handler)
    )

    result = provider.complete("sys", "user")

    assert isinstance(result, LLMError)
    assert result.kind == "invalid_response"


def test_missing_choices_field_maps_to_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps({"oops": True}).encode())

    provider = OpenRouterProvider(
        _settings(), transport=httpx.MockTransport(handler)
    )

    result = provider.complete("sys", "user")

    assert isinstance(result, LLMError)
    assert result.kind == "invalid_response"


def test_provider_satisfies_protocol() -> None:
    from app.llm.base import LLMProvider

    provider = OpenRouterProvider(_settings())

    assert isinstance(provider, LLMProvider)


def test_configured_timeout_is_applied_to_request() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, content=_success_body())

    provider = OpenRouterProvider(
        _settings(LLM_TIMEOUT_SECONDS=2.5),
        transport=httpx.MockTransport(handler),
    )

    result = provider.complete("sys", "user")

    assert isinstance(result, LLMResult)
    timeout = captured["timeout"]
    assert isinstance(timeout, dict)
    assert timeout["connect"] == 2.5
    assert timeout["read"] == 2.5
    assert timeout["write"] == 2.5
    assert timeout["pool"] == 2.5


@pytest.mark.parametrize("trailing_slash", ["", "/"])
def test_base_url_trailing_slash_is_normalized(trailing_slash: str) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, content=_success_body())

    provider = OpenRouterProvider(
        _settings(OPENROUTER_BASE_URL=f"https://openrouter.example/api/v1{trailing_slash}"),
        transport=httpx.MockTransport(handler),
    )

    provider.complete("s", "u")

    assert captured["url"] == "https://openrouter.example/api/v1/chat/completions"
