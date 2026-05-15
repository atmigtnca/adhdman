"""Classification pipeline orchestration.

Fixed-order stages: normalize -> rules -> optional LLM -> schema validate ->
one repair attempt -> inbox fallback. The pipeline is pure: it does not touch
the database, FastAPI, or any I/O beyond the injected ``LLMProvider``. It also
never raises on classification failure -- every input that survives normalize
produces a ``ClassifierOutput`` so the capture-first guarantee from Phase 1 is
preserved.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.config import Settings
from app.llm.base import LLMError, LLMProvider

from .repair import build_repair_prompt, build_system_prompt
from .rules import classify_with_rules
from .schema import ClassificationSource, ClassifierOutput


class EmptyTextError(ValueError):
    """Raised when the input text is empty after normalization."""


@dataclass(frozen=True)
class PipelineResult:
    """Final output of the classification pipeline.

    ``output`` is always a valid ``ClassifierOutput``. ``source`` records which
    stage produced it so callers can write the right diagnostics row and the
    matching action_type.
    """

    output: ClassifierOutput
    source: ClassificationSource


_WHITESPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Trim and collapse internal whitespace. Raises if the result is empty."""

    collapsed = _WHITESPACE.sub(" ", text).strip()
    if not collapsed:
        raise EmptyTextError("text must not be empty")
    return collapsed


def _parse_llm_output(raw: str) -> ClassifierOutput:
    """Parse and validate raw LLM text as a ``ClassifierOutput``.

    Raises ``ValueError`` / ``ValidationError`` on failure so the caller can
    attempt one repair pass before falling back.
    """

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("classifier output must be a JSON object")
    return ClassifierOutput.model_validate(data)


def _fallback(normalized: str, reason: str) -> ClassifierOutput:
    return ClassifierOutput(
        intent="inbox",
        confidence=0.0,
        title=normalized,
        reason=reason,
    )


def classify(
    text: str,
    *,
    settings: Settings,
    provider: LLMProvider | None,
) -> PipelineResult:
    """Run the classification pipeline.

    The kill switch ``CLASSIFY_ENABLED`` forces the pipeline to skip rules and
    LLM entirely and emit an inbox fallback so Phase 1 capture semantics can be
    restored without changing call sites.
    """

    normalized = normalize_text(text)

    if not settings.classify_enabled:
        return PipelineResult(
            output=_fallback(normalized, "classify disabled"),
            source="fallback",
        )

    rules_output = classify_with_rules(normalized)
    if rules_output.confidence >= settings.rules_accept_threshold:
        return PipelineResult(output=rules_output, source="rules")

    if provider is None:
        return PipelineResult(
            output=_fallback(
                normalized,
                "no LLM provider configured; deferring to inbox",
            ),
            source="fallback",
        )

    try:
        provider_available = provider.available
    except Exception:
        return PipelineResult(
            output=_fallback(normalized, "llm provider unavailable"),
            source="fallback",
        )
    if not provider_available:
        return PipelineResult(
            output=_fallback(
                normalized,
                "no LLM provider configured; deferring to inbox",
            ),
            source="fallback",
        )

    system_prompt = build_system_prompt()
    try:
        first = provider.complete(system_prompt, normalized)
    except Exception:
        return PipelineResult(
            output=_fallback(normalized, "llm error: provider raised"),
            source="fallback",
        )
    if isinstance(first, LLMError):
        return PipelineResult(
            output=_fallback(normalized, f"llm error: {first.kind}"),
            source="fallback",
        )

    try:
        return PipelineResult(output=_parse_llm_output(first.text), source="llm")
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        repair_error = str(exc)

    repair_prompt = build_repair_prompt(repair_error, first.text)
    try:
        second = provider.complete(system_prompt, repair_prompt)
    except Exception:
        return PipelineResult(
            output=_fallback(normalized, "llm repair error: provider raised"),
            source="fallback",
        )
    if isinstance(second, LLMError):
        return PipelineResult(
            output=_fallback(normalized, f"llm repair error: {second.kind}"),
            source="fallback",
        )

    try:
        return PipelineResult(output=_parse_llm_output(second.text), source="repair")
    except (json.JSONDecodeError, ValidationError, ValueError):
        return PipelineResult(
            output=_fallback(
                normalized,
                "llm output failed validation after repair",
            ),
            source="fallback",
        )


