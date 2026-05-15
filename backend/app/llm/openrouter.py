"""OpenRouter LLM provider.

Calls the OpenRouter chat completions API with a strict timeout and returns the
first message content. Errors are returned as ``LLMError`` values; the provider
never raises on network failure so the classification pipeline can fall back to
the inbox uniformly.

Never log the API key or full prompts/responses containing user text in
production; the provider only emits request metadata (model, status, latency).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.llm.base import LLMError, LLMResult


class OpenRouterProvider:
    """Concrete ``LLMProvider`` backed by OpenRouter chat completions."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = settings.openrouter_api_key
        self._base_url = settings.openrouter_base_url.rstrip("/")
        self._model = settings.openrouter_model
        self._timeout = settings.llm_timeout_seconds
        self._transport = transport

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def complete(self, system_prompt: str, user_text: str) -> LLMResult | LLMError:
        if not self.available:
            return LLMError(kind="unavailable", message="OPENROUTER_API_KEY not set")

        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self._base_url}/chat/completions"
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            return LLMError(kind="timeout", message=str(exc))
        except httpx.HTTPError as exc:
            return LLMError(kind="http_error", message=str(exc))

        if response.status_code >= 400:
            return LLMError(
                kind="http_error",
                message=f"status={response.status_code}",
            )

        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return LLMError(kind="invalid_response", message=str(exc))

        if not isinstance(text, str):
            return LLMError(
                kind="invalid_response", message="content was not a string"
            )

        return LLMResult(text=text)
