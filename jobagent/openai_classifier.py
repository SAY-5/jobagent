"""OpenAI structured-output classifier.

Imported only when actually used; the rest of the package (and CI)
runs without `openai` installed.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from .schema import ClassificationResponse, FormField

if TYPE_CHECKING:
    pass


_SYSTEM_PROMPT = (
    "You are a job-application form classifier. "
    "For each input field, decide which slot of a resume it maps to. "
    "Use UNMAPPED freely when the field is ambiguous, free-form, or "
    "doesn't correspond to any of the provided sections. Provide a "
    "self-reported confidence in [0,1] and a one-sentence reasoning. "
    "Do NOT invent section names — only the closed enum is allowed."
)


def make_openai_classifier(model: str | None = None):
    """Return a callable matching `Callable[[list[FormField]], ClassificationResponse]`.

    The OpenAI client is constructed at call time so the import error
    surfaces close to the user, not at module load.
    """

    def classify(fields: list[FormField]) -> ClassificationResponse:
        from openai import OpenAI  # imported here so tests don't require the dep

        client = OpenAI()
        prompt = _build_user_prompt(fields)
        # Structured outputs: the API rejects responses that don't fit
        # the Pydantic schema. That's the entire point of this module.
        resp = client.responses.parse(
            model=model or os.environ.get("JOBAGENT_OPENAI_MODEL", "gpt-4o-mini"),
            input=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            text_format=ClassificationResponse,
        )
        out = resp.output_parsed
        if out is None:
            # The SDK already raises on schema mismatch; this is just
            # belt-and-suspenders for unexpected shapes.
            raise RuntimeError("openai returned no parsed output")
        return out

    return classify


def _build_user_prompt(fields: list[FormField]) -> str:
    lines = ["Classify each of these form fields. Output one entry per field.\n"]
    for f in fields:
        lines.append(
            f"- field_id={f.field_id} | label={f.label!r} | kind={f.kind} | "
            f"required={f.required} | options={f.options[:6]}"
        )
        if f.context:
            lines.append(f"  context: {f.context}")
    return "\n".join(lines)
