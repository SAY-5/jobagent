from __future__ import annotations

from jobagent.classify import CachingClassifier, field_cache_key, mock_llm
from jobagent.schema import (
    ClassificationResponse,
    FieldClassification,
    FormField,
    ResumeSection,
)


def _f(label: str, kind: str = "text", **kw) -> FormField:
    return FormField(field_id=label.lower().replace(" ", "_"), label=label, kind=kind, **kw)


def test_regex_prefilter_handles_simple_cases():
    c = CachingClassifier(llm_call=mock_llm)
    fields = [_f("First Name"), _f("Email"), _f("Phone Number"),
              _f("LinkedIn URL"), _f("GitHub")]
    out = c.classify(fields)
    sections = {x.field_id: x.section for x in out}
    assert sections["first_name"] == ResumeSection.FIRST_NAME
    assert sections["email"] == ResumeSection.EMAIL
    assert sections["phone_number"] == ResumeSection.PHONE
    assert sections["linkedin_url"] == ResumeSection.LINKEDIN_URL
    assert sections["github"] == ResumeSection.GITHUB_URL
    # All from regex, none from LLM.
    assert all(x.source == "regex" for x in out)


def test_llm_called_only_for_unknown_fields():
    captured: list[list[str]] = []

    def llm(fields):
        captured.append([f.field_id for f in fields])
        return ClassificationResponse(classifications=[
            FieldClassification(field_id=f.field_id, section=ResumeSection.UNMAPPED,
                                confidence=0.1, reasoning="dunno", source="llm")
            for f in fields
        ])

    c = CachingClassifier(llm_call=llm)
    fields = [_f("Email"), _f("Why do you want this role?", kind="textarea")]
    c.classify(fields)
    # Email matched the regex; only the essay went to LLM.
    assert captured == [["why_do_you_want_this_role?"]]


def test_cache_short_circuits_repeat_calls():
    calls = {"n": 0}

    def llm(fields):
        calls["n"] += 1
        return ClassificationResponse(classifications=[
            FieldClassification(field_id=f.field_id, section=ResumeSection.UNMAPPED,
                                confidence=0.1, reasoning="x", source="llm")
            for f in fields
        ])

    c = CachingClassifier(llm_call=llm)
    f1 = _f("Tell us about yourself", kind="textarea")
    c.classify([f1])
    f2 = _f("Tell us about yourself", kind="textarea")  # same label, same kind
    out = c.classify([f2])
    assert calls["n"] == 1, "second call should hit the cache"
    assert out[0].source == "cache"


def test_field_cache_key_resilient_to_id_drift():
    a = _f("First Name")
    b = FormField(field_id="totally-different-id", label="First Name", kind="text")
    # Different field_id, same label+kind → same cache key.
    assert field_cache_key(a) == field_cache_key(b)


def test_operator_override():
    c = CachingClassifier(llm_call=mock_llm)
    out = c.apply_operator_override("custom_field", ResumeSection.COVER_LETTER, "we said so")
    assert out.section == ResumeSection.COVER_LETTER
    assert out.source == "operator"
    assert out.confidence == 1.0
