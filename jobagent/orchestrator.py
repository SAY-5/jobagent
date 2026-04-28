"""Orchestrator: glues detect → classify → decide → fill → persist.

The runner is split from the Playwright driver so it can be exercised
in tests against static HTML fixtures, without booting a real browser.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from .classify import CachingClassifier, mock_llm
from .detect import detect_from_html
from .policy import Policy, PolicyConfig
from .schema import (
    ClassificationResponse,
    Decision,
    FieldClassification,
    FormField,
    ResumeProfile,
    ResumeSection,
)
from .store import Store


@dataclass
class StepOutcome:
    step_id: str
    fields: list[FormField]
    classifications: list[FieldClassification]
    decisions: list[Decision]
    needs_review: bool


class Orchestrator:
    """One per Run. Constructed by either the Playwright driver or the
    fixture-replay test helper."""

    def __init__(
        self,
        store: Store,
        profile: ResumeProfile,
        run_id: str,
        llm: Callable[[list[FormField]], ClassificationResponse] | None = None,
        policy_config: PolicyConfig | None = None,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.classifier = CachingClassifier(llm_call=llm or mock_llm)
        self.policy = Policy(profile, policy_config)
        self._step_idx = 0

    def process_html(self, html: str, screenshot_path: str | None = None) -> StepOutcome:
        """Run one step against a snapshot of the page HTML.

        This is the unit of work for fixture-replay tests; the live
        driver calls it once per visible form modal.
        """
        fields = detect_from_html(html)
        html_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()[:16]
        step = self.store.add_step(self.run_id, self._step_idx, html_hash, screenshot_path)
        self._step_idx += 1

        # Persist detected fields up front; the classifier may fail
        # half-way through and we still want the audit trail.
        df_ids: dict[str, str] = {}
        for f in fields:
            row = self.store.add_field(
                step_id=step.id, field_id=f.field_id, label=f.label,
                kind=f.kind, required=f.required, options=f.options,
                context=f.context,
            )
            df_ids[f.field_id] = row.id

        classifications = self.classifier.classify(fields)
        for c in classifications:
            df_id = df_ids.get(c.field_id)
            if df_id is None:
                continue
            self.store.add_classification(
                detected_field_id=df_id, section=c.section.value,
                confidence=c.confidence, reasoning=c.reasoning, source=c.source,
            )

        decisions: list[Decision] = []
        c_by_id = {c.field_id: c for c in classifications}
        for f in fields:
            cls = c_by_id.get(
                f.field_id,
                FieldClassification(
                    field_id=f.field_id, section=ResumeSection.UNMAPPED,
                    confidence=0.0, reasoning="missing classification", source="llm",
                ),
            )
            d = self.policy.decide(f, cls)
            decisions.append(d)
            df_id = df_ids.get(f.field_id)
            if df_id:
                self.store.add_decision(
                    detected_field_id=df_id, action=d.action,
                    value=d.value, reason=d.reason,
                )

        needs_review = any(d.action == "review" for d in decisions)
        return StepOutcome(
            step_id=step.id, fields=fields,
            classifications=classifications,
            decisions=decisions,
            needs_review=needs_review,
        )

    def serialize_outcome(self, outcome: StepOutcome) -> dict:
        """For console display / API responses."""
        return {
            "step_id": outcome.step_id,
            "needs_review": outcome.needs_review,
            "fields": [
                {
                    "id": f.field_id, "label": f.label, "kind": f.kind,
                    "required": f.required, "options": f.options,
                }
                for f in outcome.fields
            ],
            "classifications": [
                json.loads(c.model_dump_json()) for c in outcome.classifications
            ],
            "decisions": [json.loads(d.model_dump_json()) for d in outcome.decisions],
        }
