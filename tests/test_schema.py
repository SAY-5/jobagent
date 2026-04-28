from __future__ import annotations

import pytest
from pydantic import ValidationError

from jobagent.schema import (
    ClassificationResponse,
    Decision,
    FieldClassification,
    FormField,
    ResumeProfile,
    ResumeSection,
)


def test_form_field_normalizes_label():
    f = FormField(field_id="x", label="  First   Name  ", kind="text")
    assert f.label == "First Name"


def test_form_field_rejects_empty_label():
    with pytest.raises(ValidationError):
        FormField(field_id="x", label="    ", kind="text")


def test_classification_confidence_clamped():
    with pytest.raises(ValidationError):
        FieldClassification(field_id="x", section=ResumeSection.EMAIL,
                            confidence=1.5, reasoning="too high")


def test_classification_response_round_trip():
    c = ClassificationResponse(
        classifications=[
            FieldClassification(field_id="a", section=ResumeSection.EMAIL,
                                confidence=0.9, reasoning="email field"),
            FieldClassification(field_id="b", section=ResumeSection.UNMAPPED,
                                confidence=0.1, reasoning="no idea"),
        ]
    )
    js = c.model_dump_json()
    again = ClassificationResponse.model_validate_json(js)
    assert again == c


def test_resume_profile_get_returns_none_for_missing():
    p = ResumeProfile(name="me", sections={ResumeSection.EMAIL: "me@example.com"})
    assert p.get(ResumeSection.EMAIL) == "me@example.com"
    assert p.get(ResumeSection.PHONE) is None


def test_decision_action_enum():
    d = Decision(field_id="x", action="fill", value="v", reason="ok")
    assert d.action == "fill"
    with pytest.raises(ValidationError):
        Decision(field_id="x", action="weird", value=None, reason="?")
