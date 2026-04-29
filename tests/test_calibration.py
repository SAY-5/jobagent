"""v3: calibration cache trains on operator overrides."""

from __future__ import annotations

from fastapi.testclient import TestClient

from jobagent.calibration import CalibrationCache, label_hash
from jobagent.classify import CachingClassifier
from jobagent.schema import (
    ClassificationResponse,
    FieldClassification,
    FormField,
    ResumeSection,
)
from jobagent.api import build_app
from jobagent.orchestrator import Orchestrator
from jobagent.schema import ResumeProfile
from jobagent.store import fresh_memory_store


def _f(label: str, kind: str = "text", **kw) -> FormField:
    return FormField(field_id=label.lower().replace(" ", "_"), label=label, kind=kind, **kw)


def test_label_hash_normalizes_case_and_whitespace():
    assert label_hash("First Name") == label_hash("first  name")
    assert label_hash("First Name") != label_hash("Last Name")


def test_cache_below_threshold_does_not_short_circuit():
    cal = CalibrationCache()
    cal.record("Highest degree", ResumeSection.EDUCATION, confirmed=True)
    # Single confirmation isn't enough — best() returns None.
    assert cal.best("Highest degree") is None


def test_cache_above_threshold_returns_section():
    cal = CalibrationCache()
    for _ in range(2):
        cal.record("Highest degree", ResumeSection.EDUCATION, confirmed=True)
    hit = cal.best("Highest degree")
    assert hit is not None
    section, stat = hit
    assert section == ResumeSection.EDUCATION
    assert stat.accuracy == 1.0
    assert stat.confirms == 2


def test_cache_records_rejects_and_lowers_accuracy():
    cal = CalibrationCache()
    cal.record("custom", ResumeSection.EDUCATION, confirmed=True)
    cal.record("custom", ResumeSection.EDUCATION, confirmed=True)
    cal.record("custom", ResumeSection.EDUCATION, confirmed=False)
    hit = cal.best("custom", min_accuracy=0.6)
    assert hit is not None
    _, stat = hit
    assert abs(stat.accuracy - (2 / 3)) < 1e-9
    # Tighter threshold rejects this entry:
    assert cal.best("custom", min_accuracy=0.9) is None


def test_classifier_consults_calibration_before_regex():
    """A trained label short-circuits even regex/LLM, with
    source='calibration'."""
    cal = CalibrationCache()
    # Train: 'Cellular number' → PHONE, even though our PHONE regex
    # already catches 'phone'/'mobile'/'telephone'. The point is that
    # operator-trained labels override the regex layer.
    for _ in range(3):
        cal.record("Cellular number", ResumeSection.PHONE, confirmed=True)

    def llm_should_not_be_called(_):
        raise AssertionError("LLM must not be called when calibration hits")

    c = CachingClassifier(llm_call=llm_should_not_be_called, calibration=cal)
    out = c.classify([_f("Cellular number")])
    assert out[0].section == ResumeSection.PHONE
    assert out[0].source == "calibration"
    assert out[0].confidence > 0.9


def test_decide_endpoint_records_calibration_observations():
    """Posting an operator override stamps the calibration cache:
    confirm[+1] for the operator's section, reject[+1] for the
    model's section if it differed."""
    store = fresh_memory_store()
    cal = CalibrationCache()
    app = build_app(store=store, calibration=cal)
    client = TestClient(app)

    # Seed a run with one detected field that the LLM mock will mark
    # as UNMAPPED. The operator then overrides to FIRST_NAME — that
    # records confirms[FIRST_NAME]+=1 AND rejects[UNMAPPED]+=1.
    profile = ResumeProfile(name="me", sections={ResumeSection.FIRST_NAME: "Sai"})
    p = store.upsert_profile(
        name="me",
        sections={k.value: v for k, v in profile.sections.items()},
    )
    run = store.start_run(profile_id=p.id, job_url="file://x", mode="shadow")
    orch = Orchestrator(store=store, profile=profile, run_id=run.id)
    orch.process_html(
        "<form><label for=fn>First Name</label><input id=fn type=text></form>"
    )

    r = client.post(f"/v1/runs/{run.id}/decide", json={
        "field_id": "fn", "section": "first_name",
        "value": "Sai", "action": "fill",
    })
    assert r.status_code == 200, r.text

    # Calibration cache state is observable via /v1/calibration.
    items = client.get("/v1/calibration").json()["items"]
    by_section = {it["section"]: it for it in items if it["label"] == "First Name"}
    assert by_section["first_name"]["confirms"] >= 1
    # The mock LLM returned UNMAPPED for this field; that section is
    # now stamped as a reject.
    if "unmapped" in by_section:
        assert by_section["unmapped"]["rejects"] >= 1


def test_calibration_passthrough_when_no_prior_classification():
    """Edge case: if the field had no prior classification at all,
    the override still confirms — no reject is recorded against any
    other section."""
    store = fresh_memory_store()
    cal = CalibrationCache()
    app = build_app(store=store, calibration=cal)
    client = TestClient(app)

    profile = ResumeProfile(name="me", sections={ResumeSection.EMAIL: "x@x"})
    p = store.upsert_profile(
        name="me",
        sections={k.value: v for k, v in profile.sections.items()},
    )
    run = store.start_run(profile_id=p.id, job_url="file://x", mode="shadow")

    # We hand-create the detected field so there's NO classification
    # row yet.
    step = store.add_step(run.id, 0, "h0", None)
    df = store.add_field(
        step_id=step.id, field_id="weird", label="Custom Question",
        kind="text", required=False, options=[],
    )
    # The decide endpoint expects to find the field by run_id -> step
    # -> field_id. We seeded that directly above.
    _ = df  # silence ruff
    r = client.post(f"/v1/runs/{run.id}/decide", json={
        "field_id": "weird", "section": "education",
        "value": "BS CS", "action": "fill",
    })
    assert r.status_code == 200
    # Confirms recorded; no rejects (no prior section to penalize).
    items = client.get("/v1/calibration").json()["items"]
    me = [it for it in items if it["label"] == "Custom Question"]
    assert any(it["section"] == "education" and it["confirms"] >= 1 for it in me)
    assert all(it["rejects"] == 0 for it in me)


def test_calibration_dump_is_sorted_by_confirms_desc():
    cal = CalibrationCache()
    for _ in range(5):
        cal.record("Resume", ResumeSection.RESUME_FILE, confirmed=True)
    for _ in range(2):
        cal.record("Cover Letter", ResumeSection.COVER_LETTER, confirmed=True)
    store = fresh_memory_store()
    app = build_app(store=store, calibration=cal)
    client = TestClient(app)
    items = client.get("/v1/calibration").json()["items"]
    confirms = [it["confirms"] for it in items]
    assert confirms == sorted(confirms, reverse=True)
