"""Typed envelopes for the form-field classification layer.

These types are the contract between the browser-detection step and
the LLM-classification step. They are also what we hand to OpenAI's
structured-output API; the API rejects any model output that doesn't
fit, which removes a whole class of "model hallucinated a field name
that doesn't exist" failures.

`ResumeSection` is closed: every form field maps to either one of the
named sections or to UNMAPPED. The latter is a deliberate escape
hatch — making the model commit to a section it isn't sure about is
where the original prototype broke worst.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ResumeSection(StrEnum):
    """The closed set of resume slots a form field can map to."""

    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    FULL_NAME = "full_name"
    EMAIL = "email"
    PHONE = "phone"
    LOCATION = "location"
    CURRENT_TITLE = "current_title"
    YEARS_EXPERIENCE = "years_experience"
    AUTHORIZED_TO_WORK = "authorized_to_work"
    REQUIRES_SPONSORSHIP = "requires_sponsorship"
    LINKEDIN_URL = "linkedin_url"
    PORTFOLIO_URL = "portfolio_url"
    GITHUB_URL = "github_url"
    RESUME_FILE = "resume_file"
    COVER_LETTER = "cover_letter"
    SALARY_EXPECTATION = "salary_expectation"
    AVAILABLE_START_DATE = "available_start_date"
    EDUCATION = "education"
    UNMAPPED = "unmapped"


FieldKind = Literal[
    "text", "textarea", "email", "tel", "url", "number",
    "select", "radio", "checkbox", "file", "date",
]


class FormField(BaseModel):
    """One input on a page, after detection and before classification."""

    field_id: str = Field(..., description="DOM-stable id we'll use to refer back")
    label: str = Field(..., description="Visible label text, normalized")
    kind: FieldKind
    required: bool = False
    options: list[str] = Field(default_factory=list,
                               description="Empty unless kind is select/radio/checkbox")
    placeholder: str | None = None
    context: str | None = Field(
        default=None,
        description="A short snippet of surrounding text — section headings, helper hints",
    )
    max_length: int | None = None

    @field_validator("label")
    @classmethod
    def _label_nonempty(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("label must not be empty")
        return v


class FieldClassification(BaseModel):
    """The model's decision for one FormField."""

    field_id: str
    section: ResumeSection
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., max_length=400)
    # `source` is filled in by the orchestrator: which layer made the
    # call (regex prefilter, cache, llm, operator).
    source: Literal["regex", "cache", "llm", "operator", "calibration"] = "llm"


class ClassificationResponse(BaseModel):
    """Wrapper the LLM returns: one classification per field."""

    classifications: list[FieldClassification]


class ResumeProfile(BaseModel):
    """The user's resume, expressed as a flat dict keyed by ResumeSection.

    `extra` carries free-form answers (e.g. "tell us about a project")
    that don't fit into the closed enum. The classifier never reads
    `extra`; the operator sets these manually in the review console.
    """

    name: str
    sections: dict[ResumeSection, str]
    extra: dict[str, str] = Field(default_factory=dict)

    def get(self, section: ResumeSection) -> str | None:
        return self.sections.get(section)


# --- decision policy -----------------------------------------------------


class Decision(BaseModel):
    """What the policy engine wants done with one field."""

    field_id: str
    action: Literal["fill", "review", "skip"]
    value: str | None = None
    reason: str
