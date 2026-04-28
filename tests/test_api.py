from __future__ import annotations

from fastapi.testclient import TestClient

from jobagent.api import build_app
from jobagent.orchestrator import Orchestrator
from jobagent.schema import ResumeProfile, ResumeSection
from jobagent.store import fresh_memory_store


def _seed_run(store):
    profile = ResumeProfile(
        name="me",
        sections={
            ResumeSection.FIRST_NAME: "Sai",
            ResumeSection.EMAIL: "me@example.com",
        },
    )
    p = store.upsert_profile(
        name="me",
        sections={k.value: v for k, v in profile.sections.items()},
    )
    run = store.start_run(profile_id=p.id, job_url="file://x", mode="shadow")
    orch = Orchestrator(store=store, profile=profile, run_id=run.id)
    orch.process_html(
        "<form><label for=fn>First Name</label><input id=fn type=text required></form>"
    )
    return run


def test_list_runs_and_run_detail():
    store = fresh_memory_store()
    run = _seed_run(store)
    app = build_app(store=store)
    client = TestClient(app)

    r = client.get("/v1/runs")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(it["id"] == run.id for it in items)

    r = client.get(f"/v1/runs/{run.id}")
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == run.id
    assert len(d["steps"]) == 1


def test_decide_endpoint_records_operator_override():
    store = fresh_memory_store()
    run = _seed_run(store)
    app = build_app(store=store)
    client = TestClient(app)

    r = client.post(
        f"/v1/runs/{run.id}/decide",
        json={"field_id": "fn", "section": "first_name", "value": "Sai", "action": "fill"},
    )
    assert r.status_code == 200, r.text

    detail = client.get(f"/v1/runs/{run.id}").json()
    field = next(f for f in detail["steps"][0]["fields"] if f["label"] == "First Name")
    assert field["decision"]["reviewed"] is True


def test_health():
    store = fresh_memory_store()
    app = build_app(store=store)
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True
