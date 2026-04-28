"""Form-field classification — the structured layer the project is about.

Layers, in order of precedence:

  1. **regex prefilter** — for "email", "first name", "linkedin url",
     etc. these are the >95% confidence cases; calling an LLM for them
     burns latency and money for no benefit.
  2. **cache** — keyed on (label_hash, options_hash). The same field
     on a re-tried run gets the same answer, eliminating drift.
  3. **LLM (structured output)** — only for fields the prefilter and
     cache missed. We hand OpenAI the closed `ClassificationResponse`
     schema; it can't return anything outside it.
  4. **operator override** — if the review console edits a field, the
     edit is recorded as a `FieldClassification` with source="operator"
     and replaces all earlier layers' answers for that field.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .schema import (
    ClassificationResponse,
    FieldClassification,
    FormField,
    ResumeSection,
)

# A regex → ResumeSection table for the obvious cases. Order matters:
# more-specific patterns first.
_REGEX_RULES: list[tuple[re.Pattern[str], ResumeSection, float]] = [
    (re.compile(r"\bfirst[\s_-]?name\b", re.I),                 ResumeSection.FIRST_NAME,   0.99),
    (re.compile(r"\b(last|family|sur)[\s_-]?name\b", re.I),     ResumeSection.LAST_NAME,    0.99),
    (re.compile(r"\bfull[\s_-]?name\b|\byour\s+name\b", re.I),  ResumeSection.FULL_NAME,    0.97),
    (re.compile(r"\bemail\b|\be[-\s]?mail\b", re.I),            ResumeSection.EMAIL,        0.99),
    (re.compile(r"\bphone\b|\bmobile\b|\btelephone\b", re.I),   ResumeSection.PHONE,        0.97),
    (re.compile(r"\blinkedin\b", re.I),                          ResumeSection.LINKEDIN_URL, 0.99),
    (re.compile(r"\bgithub\b", re.I),                            ResumeSection.GITHUB_URL,   0.99),
    (re.compile(r"\bportfolio\b|\bpersonal\s+site\b|\bwebsite\b", re.I),
                                                                ResumeSection.PORTFOLIO_URL, 0.92),
    (re.compile(r"\bresume\b|\bcv\b", re.I),                    ResumeSection.RESUME_FILE,  0.97),
    (re.compile(r"\bcover[\s_-]?letter\b", re.I),               ResumeSection.COVER_LETTER, 0.97),
    (re.compile(r"\b(years|yrs)\s*(of)?\s*(experience|exp)\b", re.I),
                                                                ResumeSection.YEARS_EXPERIENCE, 0.96),
    (re.compile(r"\b(authorized|authoris(ed|ation)).*work\b|\beligible\s+to\s+work\b", re.I),
                                                                ResumeSection.AUTHORIZED_TO_WORK, 0.93),
    (re.compile(r"\b(visa\s+sponsor|require\s+sponsor|sponsorship\s+required)\b", re.I),
                                                                ResumeSection.REQUIRES_SPONSORSHIP, 0.93),
    (re.compile(r"\bsalary\b|\bcompensation\b|\bpay\s+expect", re.I),
                                                                ResumeSection.SALARY_EXPECTATION, 0.92),
    (re.compile(r"\b(start\s+date|available(\s+to\s+start)?)\b", re.I),
                                                                ResumeSection.AVAILABLE_START_DATE, 0.93),
    (re.compile(r"\b(city|location|where\s+are\s+you\s+based)\b", re.I),
                                                                ResumeSection.LOCATION,     0.92),
    (re.compile(r"\bcurrent\s+title\b|\bcurrent\s+role\b|\bcurrent\s+position\b|\bjob\s+title\b", re.I),
                                                                ResumeSection.CURRENT_TITLE, 0.90),
    (re.compile(r"\beducation\b|\bdegree\b|\buniversity\b|\bschool\b", re.I),
                                                                ResumeSection.EDUCATION,    0.85),
]


def _hash(parts: list[str]) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def field_cache_key(f: FormField) -> str:
    """Deterministic cache key for a field. Drift-resistant: small DOM
    changes that don't change the label or the options don't change
    the key."""
    return _hash([f.label.lower().strip(), "|".join(sorted(f.options)), f.kind])


@dataclass
class CachingClassifier:
    """The orchestrator that stitches all the layers together.

    `llm_call` is injected. The default `mock_llm` always returns
    UNMAPPED with confidence 0; production code passes
    `OpenAIClassifier`.
    """

    llm_call: Callable[[list[FormField]], ClassificationResponse]
    cache: dict[str, FieldClassification] = field(default_factory=dict)
    enable_regex: bool = True

    def classify(self, fields: list[FormField]) -> list[FieldClassification]:
        results: list[FieldClassification] = []
        unresolved: list[FormField] = []
        for f in fields:
            # 1. regex prefilter
            if self.enable_regex:
                rx = self._regex_match(f)
                if rx is not None:
                    results.append(rx)
                    continue
            # 2. cache lookup
            key = field_cache_key(f)
            if key in self.cache:
                cached = self.cache[key].model_copy(update={"field_id": f.field_id})
                cached.source = "cache"
                results.append(cached)
                continue
            unresolved.append(f)

        # 3. one LLM call for the remaining (batch is critical: keeps
        #    the prompt cost flat regardless of field count).
        if unresolved:
            resp = self.llm_call(unresolved)
            for c in resp.classifications:
                c.source = "llm"
                # Save to cache under the *field*'s key.
                f = next((u for u in unresolved if u.field_id == c.field_id), None)
                if f is not None:
                    self.cache[field_cache_key(f)] = c
                results.append(c)

        return results

    def apply_operator_override(self, field_id: str, section: ResumeSection,
                                reasoning: str = "operator override") -> FieldClassification:
        """Replace whatever any prior layer said for this field.

        The override is *not* cached: a different field with the same
        label hash should still go through the normal pipeline.
        """
        return FieldClassification(
            field_id=field_id,
            section=section,
            confidence=1.0,
            reasoning=reasoning,
            source="operator",
        )

    @staticmethod
    def _regex_match(f: FormField) -> FieldClassification | None:
        haystack = f"{f.label} {f.placeholder or ''} {f.context or ''}"
        for pattern, section, conf in _REGEX_RULES:
            if pattern.search(haystack):
                return FieldClassification(
                    field_id=f.field_id,
                    section=section,
                    confidence=conf,
                    reasoning=f"regex:{pattern.pattern}",
                    source="regex",
                )
        return None


def mock_llm(fields: list[FormField]) -> ClassificationResponse:
    """Deterministic LLM stand-in. Returns UNMAPPED for every field
    so tests for the regex/cache layers can prove they short-circuit."""
    return ClassificationResponse(
        classifications=[
            FieldClassification(
                field_id=f.field_id,
                section=ResumeSection.UNMAPPED,
                confidence=0.0,
                reasoning="mock_llm: not classifying",
                source="llm",
            )
            for f in fields
        ]
    )
