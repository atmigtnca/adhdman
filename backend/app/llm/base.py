"""LLM provider protocol and result types.

The pipeline only depends on this protocol; concrete providers live in sibling
modules. Errors are returned as values rather than raised so the pipeline can
map any failure to the inbox fallback without exception handling at every call
site.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


LLMErrorKind = Literal["unavailable", "timeout", "http_error", "invalid_response"]


@dataclass(frozen=True)
class LLMResult:
    """Successful provider response: the raw text returned by the model."""

    text: str


@dataclass(frozen=True)
class LLMError:
    """Provider failure value. The pipeline maps any error to inbox fallback."""

    kind: LLMErrorKind
    message: str


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal protocol all providers implement."""

    @property
    def available(self) -> bool:
        """Whether the provider is configured well enough to be called."""

    def complete(self, system_prompt: str, user_text: str) -> LLMResult | LLMError:
        """Run a single completion. Must not raise on network failures."""
