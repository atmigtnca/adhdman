"""Prompt builders for the LLM stage and the single repair attempt.

The system prompt names the strict JSON schema and forbids extra text. The
repair prompt re-presents the previous invalid output together with the
validator's error message so the model has a concrete diff target.
"""

from __future__ import annotations


_SYSTEM_PROMPT = """\
You classify a single short user note into exactly one intent.

Allowed intents: "task", "event", "inbox".

Respond with ONE JSON object and nothing else. No prose, no code fences.
Schema:
{
  "intent": "task" | "event" | "inbox",
  "confidence": number in [0.0, 1.0],
  "title": short normalized title (required for task and event),
  "starts_at": ISO8601 string or null (only meaningful for event),
  "ends_at": ISO8601 string or null (only meaningful for event),
  "reason": short rationale, no chain of thought
}

Use "inbox" whenever the note is ambiguous; never invent times.
"""


def build_system_prompt() -> str:
    return _SYSTEM_PROMPT


def build_repair_prompt(error_message: str, previous_output: str) -> str:
    """Build the single-shot repair prompt.

    The model is shown its previous response verbatim plus the validator's
    error so it can correct the specific failure. We do not retry more than
    once; further failures map to the inbox fallback.
    """

    return (
        "Your previous response did not satisfy the schema.\n"
        f"Validator error: {error_message}\n"
        "Previous response:\n"
        f"{previous_output}\n"
        "Return a corrected JSON object only. No prose, no code fences."
    )
