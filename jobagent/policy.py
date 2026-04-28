"""Policy engine: from (classification, profile, field) → Decision.

Three dials live here:

  AUTO_CONFIDENCE  — at or above, fill without review (default 0.85).
  REVIEW_CONFIDENCE — at or above, fill but mark reviewer-needed (0.50).
  REQUIRED_FALLBACK_TO_REVIEW — required fields below AUTO_CONFIDENCE
                                always go to review even if filled.

Why three dials and not one threshold: the cost of a wrong auto-fill
on a non-required field is small (an awkward answer); on a required
field it can ship a flawed application. The policy is deliberately
conservative on requireds.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import process as fuzz_process

from .schema import (
    Decision,
    FieldClassification,
    FormField,
    ResumeProfile,
    ResumeSection,
)


@dataclass
class PolicyConfig:
    auto_confidence: float = 0.85
    review_confidence: float = 0.50
    required_fallback_to_review: bool = True
    # Field kinds we never auto-fill regardless of confidence — too
    # easy to get wrong, and the cost of a wrong answer is high.
    never_auto_fill_kinds: tuple[str, ...] = ("file",)


class Policy:
    """Stateless decision engine."""

    def __init__(self, profile: ResumeProfile, config: PolicyConfig | None = None) -> None:
        self.profile = profile
        self.config = config or PolicyConfig()

    def decide(self, field: FormField, classification: FieldClassification) -> Decision:
        if classification.section == ResumeSection.UNMAPPED:
            return Decision(
                field_id=field.field_id,
                action="review" if field.required else "skip",
                reason="unmapped (LLM opted out)",
            )

        value = self._resolve_value(field, classification.section)
        if value is None:
            return Decision(
                field_id=field.field_id,
                action="review" if field.required else "skip",
                reason=f"profile has no value for {classification.section.value}",
            )

        if field.kind in self.config.never_auto_fill_kinds:
            return Decision(
                field_id=field.field_id,
                action="review",
                value=value,
                reason=f"{field.kind} fields never auto-fill",
            )

        if classification.confidence >= self.config.auto_confidence:
            if field.required and classification.confidence < self.config.auto_confidence + 0.05:
                # Just at the threshold *and* required — round to review.
                return Decision(
                    field_id=field.field_id,
                    action="review",
                    value=value,
                    reason="required field, confidence at threshold",
                )
            return Decision(
                field_id=field.field_id,
                action="fill",
                value=value,
                reason=f"auto (conf={classification.confidence:.2f})",
            )

        if classification.confidence >= self.config.review_confidence:
            return Decision(
                field_id=field.field_id,
                action="review",
                value=value,
                reason=f"low-confidence ({classification.confidence:.2f}); review",
            )

        return Decision(
            field_id=field.field_id,
            action="review" if field.required else "skip",
            reason=f"too low confidence ({classification.confidence:.2f})",
        )

    def _resolve_value(self, field: FormField, section: ResumeSection) -> str | None:
        raw = self.profile.get(section)
        if raw is None:
            return None
        # For select/radio, fuzzy-match the predicted value against the
        # actual option labels on the page. This is the place where
        # LLM-decided "I want option X" meets the real DOM, and it's a
        # frequent source of subtle bugs.
        if field.kind in ("select", "radio") and field.options:
            best = fuzz_process.extractOne(raw, field.options, score_cutoff=70)
            return best[0] if best else None
        return raw
