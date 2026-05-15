"""Tests for the classification pipeline.

All tests run offline. The LLM stage is exercised via an in-memory fake
``LLMProvider`` so no real network calls are made. The pipeline itself is
pure (no DB, no FastAPI) which keeps these tests fast and focused on the
stage ordering, threshold short-circuit, error mapping, and the single
repair attempt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.classification import (
    EmptyTextError,
    PipelineResult,
    classify,
    normalize_text,
)
from app.classification.schema import ClassifierOutput
from app.config import Settings
from app.llm.base import LLMError, LLMProvider, LLMResult


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "OPENROUTER_API_KEY": "test-key",
        "RULES_ACCEPT_THRESHOLD": 0.85,
        "CLASSIFY_ENABLED": True,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


@dataclass
class FakeProvider:
    """Scripted ``LLMProvider`` for pipeline tests.

    Returns the next queued response per call. Records every (system, user)
    pair so tests can assert prompt shape and call count.
    """

    responses: list[LLMResult | LLMError]
    available_flag: bool = True
    calls: list[tuple[str, str]] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.available_flag

    def complete(self, system_prompt: str, user_text: str) -> LLMResult | LLMError:
        self.calls.append((system_prompt, user_text))
        if not self.responses:
            raise AssertionError("FakeProvider received an unexpected extra call")
        return self.responses.pop(0)


def _assert_pipeline_result(result: object) -> PipelineResult:
    assert isinstance(result, PipelineResult)
    assert isinstance(result.output, ClassifierOutput)
    return result


def test_normalize_collapses_whitespace_and_rejects_empty() -> None:
    assert normalize_text("  hello   world\n ") == "hello world"
    with pytest.raises(EmptyTextError):
        normalize_text("   \n\t  ")


def test_rules_short_circuit_skips_llm_for_imperative() -> None:
    provider = FakeProvider(responses=[])
    result = _assert_pipeline_result(
        classify("buy milk", settings=_settings(), provider=provider)
    )

    assert result.source == "rules"
    assert result.output.intent == "task"
    assert result.output.title == "buy milk"
    assert provider.calls == []


def test_rules_short_circuit_for_iso_timestamp_event() -> None:
    provider = FakeProvider(responses=[])
    result = _assert_pipeline_result(
        classify(
            "Dentist 2026-07-04T09:00",
            settings=_settings(),
            provider=provider,
        )
    )

    assert result.source == "rules"
    assert result.output.intent == "event"
    assert result.output.starts_at == "2026-07-04T09:00"
    assert provider.calls == []


def test_llm_invoked_when_rules_inconclusive() -> None:
    provider = FakeProvider(
        responses=[
            LLMResult(
                text='{"intent":"task","confidence":0.9,"title":"groceries","reason":"clear task"}'
            )
        ]
    )
    result = _assert_pipeline_result(
        classify("groceries", settings=_settings(), provider=provider)
    )

    assert result.source == "llm"
    assert result.output.intent == "task"
    assert result.output.title == "groceries"
    assert len(provider.calls) == 1
    system_prompt, user_text = provider.calls[0]
    assert "JSON" in system_prompt
    assert user_text == "groceries"


def test_missing_api_key_skips_provider_and_falls_back() -> None:
    provider = FakeProvider(responses=[], available_flag=False)
    result = _assert_pipeline_result(
        classify(
            "groceries",
            settings=_settings(OPENROUTER_API_KEY=None),
            provider=provider,
        )
    )

    assert result.source == "fallback"
    assert result.output.intent == "inbox"
    assert result.output.title == "groceries"
    assert provider.calls == []


def test_no_provider_at_all_falls_back() -> None:
    result = _assert_pipeline_result(
        classify("groceries", settings=_settings(), provider=None)
    )

    assert result.source == "fallback"
    assert result.output.intent == "inbox"


def test_llm_timeout_maps_to_fallback() -> None:
    provider = FakeProvider(
        responses=[LLMError(kind="timeout", message="boom")]
    )
    result = _assert_pipeline_result(
        classify("groceries", settings=_settings(), provider=provider)
    )

    assert result.source == "fallback"
    assert result.output.intent == "inbox"
    assert "timeout" in (result.output.reason or "")
    assert len(provider.calls) == 1


def test_llm_http_error_maps_to_fallback() -> None:
    provider = FakeProvider(
        responses=[LLMError(kind="http_error", message="500")]
    )
    result = _assert_pipeline_result(
        classify("groceries", settings=_settings(), provider=provider)
    )

    assert result.source == "fallback"
    assert "http_error" in (result.output.reason or "")


def test_invalid_json_triggers_single_repair_attempt_success() -> None:
    provider = FakeProvider(
        responses=[
            LLMResult(text="not json at all"),
            LLMResult(
                text='{"intent":"task","confidence":0.7,"title":"groceries","reason":"repaired"}'
            ),
        ]
    )
    result = _assert_pipeline_result(
        classify("groceries", settings=_settings(), provider=provider)
    )

    assert result.source == "repair"
    assert result.output.intent == "task"
    assert len(provider.calls) == 2
    repair_user_text = provider.calls[1][1]
    assert "previous response" in repair_user_text.lower()
    assert "not json at all" in repair_user_text


def test_repair_failure_maps_to_fallback() -> None:
    provider = FakeProvider(
        responses=[
            LLMResult(text="still not json"),
            LLMResult(text="still bad"),
        ]
    )
    result = _assert_pipeline_result(
        classify("groceries", settings=_settings(), provider=provider)
    )

    assert result.source == "fallback"
    assert result.output.intent == "inbox"
    assert len(provider.calls) == 2


def test_repair_error_response_maps_to_fallback() -> None:
    provider = FakeProvider(
        responses=[
            LLMResult(text="oops"),
            LLMError(kind="timeout", message="slow"),
        ]
    )
    result = _assert_pipeline_result(
        classify("groceries", settings=_settings(), provider=provider)
    )

    assert result.source == "fallback"
    assert "repair" in (result.output.reason or "")


def test_schema_violation_triggers_repair() -> None:
    # Missing required ``title`` for a task -> first attempt invalid; second
    # attempt provides title and succeeds via the repair stage.
    provider = FakeProvider(
        responses=[
            LLMResult(text='{"intent":"task","confidence":0.7}'),
            LLMResult(
                text='{"intent":"task","confidence":0.7,"title":"groceries","reason":"ok"}'
            ),
        ]
    )
    result = _assert_pipeline_result(
        classify("groceries", settings=_settings(), provider=provider)
    )

    assert result.source == "repair"
    assert result.output.title == "groceries"
    assert len(provider.calls) == 2


def test_classify_disabled_short_circuits_to_fallback() -> None:
    provider = FakeProvider(responses=[])
    result = _assert_pipeline_result(
        classify(
            "buy milk",
            settings=_settings(CLASSIFY_ENABLED=False),
            provider=provider,
        )
    )

    assert result.source == "fallback"
    assert result.output.intent == "inbox"
    assert provider.calls == []


def test_empty_input_raises_before_any_stage() -> None:
    provider = FakeProvider(responses=[])
    with pytest.raises(EmptyTextError):
        classify("   ", settings=_settings(), provider=provider)
    assert provider.calls == []


class _RaisingAvailableProvider:
    @property
    def available(self) -> bool:
        raise RuntimeError("boom")

    def complete(self, system_prompt: str, user_text: str):  # pragma: no cover
        raise AssertionError("should not be called")


class _RaisingCompleteProvider:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system_prompt: str, user_text: str):
        self.calls += 1
        raise RuntimeError("network exploded")


class _RaisingOnRepairProvider:
    available = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_text: str):
        self.calls.append((system_prompt, user_text))
        if len(self.calls) == 1:
            return LLMResult(text="not json")
        raise RuntimeError("repair exploded")


def test_provider_available_raising_maps_to_fallback() -> None:
    provider = _RaisingAvailableProvider()
    result = _assert_pipeline_result(
        classify("groceries", settings=_settings(), provider=provider)
    )
    assert result.source == "fallback"
    assert result.output.intent == "inbox"
    assert result.output.reason == "llm provider unavailable"


def test_provider_complete_raising_maps_to_fallback() -> None:
    provider = _RaisingCompleteProvider()
    result = _assert_pipeline_result(
        classify("groceries", settings=_settings(), provider=provider)
    )
    assert result.source == "fallback"
    assert result.output.intent == "inbox"
    assert result.output.reason == "llm error: provider raised"
    assert provider.calls == 1


def test_provider_repair_raising_maps_to_fallback() -> None:
    provider = _RaisingOnRepairProvider()
    result = _assert_pipeline_result(
        classify("groceries", settings=_settings(), provider=provider)
    )
    assert result.source == "fallback"
    assert result.output.intent == "inbox"
    assert result.output.reason == "llm repair error: provider raised"
    assert len(provider.calls) == 2


def test_fake_provider_satisfies_protocol() -> None:
    provider = FakeProvider(responses=[])
    assert isinstance(provider, LLMProvider)
