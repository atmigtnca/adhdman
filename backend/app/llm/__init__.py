"""LLM provider abstractions for ADHDman.

Phase 2 introduces a thin provider protocol so the classification pipeline can
inject a fake transport in tests without monkeypatching ``httpx`` directly. Only
the OpenRouter provider is implemented in this phase.
"""

from app.llm.base import LLMError, LLMErrorKind, LLMProvider, LLMResult
from app.llm.openrouter import OpenRouterProvider

__all__ = [
    "LLMError",
    "LLMErrorKind",
    "LLMProvider",
    "LLMResult",
    "OpenRouterProvider",
]
