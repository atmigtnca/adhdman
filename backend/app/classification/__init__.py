"""Classification layer: turns raw captured text into a structured candidate.

This package is intentionally decoupled from FastAPI in Phase 2 task 1; it
exposes pure functions and Pydantic models that the API layer can call later.
"""

from .schema import ClassifierOutput, Intent, ClassificationSource
from .rules import classify_with_rules

__all__ = [
    "ClassifierOutput",
    "Intent",
    "ClassificationSource",
    "classify_with_rules",
]
