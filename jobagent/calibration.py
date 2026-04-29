"""Per-(label, section) calibration cache.

When the operator reviews a dossier and corrects a classification —
say the model said `EDUCATION` for a field labeled "Highest degree
earned" but the operator overrode it to `EDUCATION` themselves
(confirming) or to `UNMAPPED` (rejecting) — that signal is recorded
here. Future runs consult the cache *before* asking the LLM: if a
(label_hash, section) pair has been confirmed by an operator at
least once, the classifier returns the operator's choice with
confidence=1.0 and source="calibration".

The cache is stored in the SQLModel store (table:
``calibration_record``) so it persists across processes; for
testing we hand the classifier an in-memory ``CalibrationCache``.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from .schema import FieldClassification, FormField, ResumeSection


def label_hash(label: str) -> str:
    """Stable key for a field's label. Whitespace + case normalized so
    'First name' and 'first  name' collapse to the same record."""
    norm = " ".join(label.lower().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


@dataclass
class CalibrationStat:
    """One (label_hash, section) record's accumulated counters."""

    confirms: int = 0    # operator picked this section
    rejects: int = 0     # operator picked something different (overrode this)
    last_text: str = ""  # most recent label text (for human-readable dumps)

    @property
    def total(self) -> int:
        return self.confirms + self.rejects

    @property
    def accuracy(self) -> float:
        """Confirm rate. Returns 0.0 with no observations to keep
        the policy from auto-applying single-confirm cache hits."""
        if self.total == 0:
            return 0.0
        return self.confirms / self.total


class CalibrationStore(Protocol):
    def record(self, label: str, section: ResumeSection, *, confirmed: bool) -> None: ...
    def lookup(self, label: str) -> list[tuple[ResumeSection, CalibrationStat]]: ...


@dataclass
class CalibrationCache:
    """In-memory CalibrationStore. SQL-backed version mirrors the
    same surface — see store.add_calibration_observation."""

    _by_label: dict[str, dict[ResumeSection, CalibrationStat]] = field(
        default_factory=lambda: defaultdict(dict)
    )

    def record(self, label: str, section: ResumeSection, *, confirmed: bool) -> None:
        key = label_hash(label)
        bucket = self._by_label[key]
        stat = bucket.setdefault(section, CalibrationStat())
        if confirmed:
            stat.confirms += 1
        else:
            stat.rejects += 1
        stat.last_text = label

    def lookup(self, label: str) -> list[tuple[ResumeSection, CalibrationStat]]:
        key = label_hash(label)
        return list(self._by_label.get(key, {}).items())

    def best(
        self,
        label: str,
        *,
        min_observations: int = 2,
        min_accuracy: float = 0.75,
    ) -> tuple[ResumeSection, CalibrationStat] | None:
        """Pick the highest-accuracy section for this label that has
        cleared the (min_observations, min_accuracy) thresholds.
        Returns None if nothing qualifies — the classifier falls
        through to its regex/cache/LLM ladder.

        Conservative on purpose: a single operator confirmation does
        NOT short-circuit the LLM. Two confirmations + ≥75% accuracy
        does. Tunable per-deployment."""
        candidates = self.lookup(label)
        if not candidates:
            return None
        candidates.sort(key=lambda kv: (kv[1].accuracy, kv[1].confirms), reverse=True)
        top_section, top = candidates[0]
        if top.total >= min_observations and top.accuracy >= min_accuracy:
            return top_section, top
        return None


def calibration_classification(
    field: FormField,
    section: ResumeSection,
    stat: CalibrationStat,
) -> FieldClassification:
    """Build the FieldClassification a calibration hit short-circuits to."""
    return FieldClassification(
        field_id=field.field_id,
        section=section,
        confidence=stat.accuracy,
        reasoning=(
            f"calibration: {stat.confirms}/{stat.total} operator confirmations "
            f"(acc={stat.accuracy:.2f})"
        ),
        source="calibration",
    )
