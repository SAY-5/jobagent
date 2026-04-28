from __future__ import annotations

from jobagent.policy import Policy, PolicyConfig
from jobagent.schema import (
    FieldClassification,
    FormField,
    ResumeProfile,
    ResumeSection,
)


def _profile() -> ResumeProfile:
    return ResumeProfile(
        name="me",
        sections={
            ResumeSection.FIRST_NAME: "Sai",
            ResumeSection.EMAIL: "me@example.com",
            ResumeSection.YEARS_EXPERIENCE: "5",
            ResumeSection.AUTHORIZED_TO_WORK: "Yes",
        },
    )


def test_high_confidence_fills():
    p = Policy(_profile())
    f = FormField(field_id="x", label="First Name", kind="text", required=True)
    c = FieldClassification(field_id="x", section=ResumeSection.FIRST_NAME,
                            confidence=0.95, reasoning="obvious")
    d = p.decide(f, c)
    assert d.action == "fill"
    assert d.value == "Sai"


def test_low_confidence_required_goes_to_review():
    p = Policy(_profile())
    f = FormField(field_id="x", label="something", kind="text", required=True)
    c = FieldClassification(field_id="x", section=ResumeSection.FIRST_NAME,
                            confidence=0.6, reasoning="medium")
    d = p.decide(f, c)
    assert d.action == "review"


def test_unmapped_required_goes_to_review_unmapped_optional_skips():
    p = Policy(_profile())
    f_req = FormField(field_id="r", label="essay", kind="textarea", required=True)
    f_opt = FormField(field_id="o", label="essay", kind="textarea", required=False)
    c = FieldClassification(field_id="r", section=ResumeSection.UNMAPPED,
                            confidence=0.0, reasoning="opted out")
    d_req = p.decide(f_req, c)
    d_opt = p.decide(f_opt, c.model_copy(update={"field_id": "o"}))
    assert d_req.action == "review"
    assert d_opt.action == "skip"


def test_select_fuzzy_matches_options():
    p = Policy(_profile())
    f = FormField(
        field_id="auth", label="Authorized to work?", kind="select",
        required=True, options=["Yes, I am", "No", "Prefer not to say"],
    )
    c = FieldClassification(field_id="auth", section=ResumeSection.AUTHORIZED_TO_WORK,
                            confidence=0.95, reasoning="auth field")
    d = p.decide(f, c)
    assert d.action == "fill"
    assert d.value == "Yes, I am"  # fuzzy-matched from "Yes"


def test_file_kind_never_auto_fills():
    p = Policy(_profile())
    f = FormField(field_id="rs", label="Resume", kind="file", required=True)
    c = FieldClassification(field_id="rs", section=ResumeSection.RESUME_FILE,
                            confidence=0.99, reasoning="resume")
    # Profile has no RESUME_FILE — but even if it did, file is review-only.
    d = p.decide(f, c)
    assert d.action in ("review", "skip")


def test_threshold_just_at_boundary_required_routes_to_review():
    cfg = PolicyConfig(auto_confidence=0.85)
    p = Policy(_profile(), cfg)
    f = FormField(field_id="x", label="name", kind="text", required=True)
    c = FieldClassification(field_id="x", section=ResumeSection.FIRST_NAME,
                            confidence=0.86, reasoning="just at threshold")
    d = p.decide(f, c)
    # Required + within 0.05 of the threshold → review.
    assert d.action == "review"


def test_value_missing_from_profile_routes_to_review_when_required():
    p = Policy(_profile())  # has no PHONE
    f = FormField(field_id="p", label="Phone", kind="tel", required=True)
    c = FieldClassification(field_id="p", section=ResumeSection.PHONE,
                            confidence=0.97, reasoning="phone")
    d = p.decide(f, c)
    assert d.action == "review"
    assert "no value" in d.reason.lower()
