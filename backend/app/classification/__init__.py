"""Classification layer: turns raw captured text into a structured candidate.

This package is intentionally decoupled from FastAPI in Phase 2 task 1; it
exposes pure functions and Pydantic models that the API layer can call later.
"""

from .pipeline import EmptyTextError, PipelineResult, classify, normalize_text
from .rules import classify_with_rules
from .schema import ClassificationSource, ClassifierOutput, Intent

__all__ = [
    "ClassifierOutput",
    "Intent",
    "ClassificationSource",
    "classify_with_rules",
    "classify",
    "normalize_text",
    "EmptyTextError",
    "PipelineResult",
]
