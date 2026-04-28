from __future__ import annotations

from pathlib import Path

import pytest

from jobagent.orchestrator import Orchestrator
from jobagent.schema import (
    ClassificationResponse,
    FieldClassification,
    ResumeProfile,
    ResumeSection,
)
from jobagent.store import fresh_memory_store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def profile() -> ResumeProfile:
    return ResumeProfile(
        name="me",
        sections={
            ResumeSection.FIRST_NAME: "Sai",
            ResumeSection.LAST_NAME: "Yamani",
            ResumeSection.EMAIL: "me@example.com",
            ResumeSection.PHONE: "+1 555 0100",
            ResumeSection.AUTHORIZED_TO_WORK: "Yes",
            ResumeSection.YEARS_EXPERIENCE: "5",
            ResumeSection.COVER_LETTER: "I'm excited to apply.",
        },
    )


def _scripted_llm(answers: dict[str, ResumeSection]):
    """Returns a llm callable that answers from a fixed mapping."""

    def llm(fields):
        return ClassificationResponse(
            classifications=[
                FieldClassification(
                    field_id=f.field_id,
                    section=answers.get(f.field_id, ResumeSection.UNMAPPED),
                    confidence=0.9 if f.field_id in answers else 0.1,
                    reasoning=f"scripted for {f.field_id}",
                    source="llm",
                )
                for f in fields
            ]
        )

    return llm


def test_full_pipeline_against_easy_apply_fixture(profile):
    store = fresh_memory_store()
    pid = store.upsert_profile(
        name="me",
        sections={k.value: v for k, v in profile.sections.items()},
    ).id
    run = store.start_run(profile_id=pid, job_url="file://fixture", mode="shadow")

    # Most fields are caught by the regex prefilter; only the essay
    # ("why") and the file ("resume") need an LLM/policy decision.
    llm = _scripted_llm({
        "why": ResumeSection.COVER_LETTER,
        "resume": ResumeSection.RESUME_FILE,
    })
    orch = Orchestrator(store=store, profile=profile, run_id=run.id, llm=llm)
    html = (FIXTURES / "easy_apply_step1.html").read_text()
    outcome = orch.process_html(html)

    by_id = {d.field_id: d for d in outcome.decisions}
    assert by_id["firstName"].action == "fill"
    assert by_id["firstName"].value == "Sai"
    assert by_id["email"].action == "fill"
    assert by_id["auth"].action == "fill"
    assert by_id["auth"].value == "Yes"

    # File fields never auto-fill, regardless of the LLM saying so.
    assert by_id["resume"].action == "review"

    # The orchestrator should have flagged the run as needing review
    # because of the file upload at minimum.
    assert outcome.needs_review is True


def test_audit_trail_persists_classifier_and_decision(profile):
    store = fresh_memory_store()
    pid = store.upsert_profile(
        name="me",
        sections={k.value: v for k, v in profile.sections.items()},
    ).id
    run = store.start_run(profile_id=pid, job_url="file://x", mode="shadow")
    orch = Orchestrator(store=store, profile=profile, run_id=run.id)

    html = (FIXTURES / "easy_apply_step1.html").read_text()
    orch.process_html(html)

    detail = store.run_detail(run.id)
    assert detail is not None
    assert len(detail["steps"]) == 1
    fields = detail["steps"][0]["fields"]

    # Every detected field must have both a classification and a decision.
    for f in fields:
        assert f["classification"] is not None, f
        assert f["decision"] is not None, f
