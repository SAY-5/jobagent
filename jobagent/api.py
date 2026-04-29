"""FastAPI app for the review console.

Endpoints:
  GET    /v1/runs                     — list recent runs
  GET    /v1/runs/{id}                — run detail (steps, fields, decisions)
  POST   /v1/runs/{id}/decide         — operator override for a field
  POST   /v1/runs/{id}/replay         — reclassify with a fresh LLM
  GET    /v1/profile/{name}           — fetch profile
  PUT    /v1/profile/{name}           — upsert profile
  GET    /healthz
"""

from __future__ import annotations

import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .schema import ResumeSection
from .store import (
    Application,
    Classification,
    DecisionRow,
    DetectedField,
    FormStep,
    Profile,
    Run,
    Store,
)


class DecideBody(BaseModel):
    field_id: str
    section: ResumeSection
    value: str | None = None
    action: str = "fill"  # fill | review | skip


class ProfileBody(BaseModel):
    sections: dict[ResumeSection, str]
    extra: dict[str, str] = {}


def build_app(
    store: Store | None = None,
    calibration: object | None = None,
) -> FastAPI:
    from .calibration import CalibrationCache  # local import (avoid cycles in __init__)
    state = {
        "store": store or Store(os.environ.get("JOBAGENT_DSN", "sqlite:///./jobagent.db")),
        "calibration": calibration if calibration is not None else CalibrationCache(),
    }
    app = FastAPI(title="JobAgent · Review Console", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("JOBAGENT_CORS", "http://localhost:5173").split(","),
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/v1/runs")
    def list_runs(limit: int = 50) -> dict:
        rs = state["store"].list_runs(limit=limit)
        return {
            "items": [
                {
                    "id": r.id, "job_url": r.job_url, "title": r.title, "company": r.company,
                    "mode": r.mode, "status": r.status,
                    "started_at": r.started_at.isoformat(),
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                }
                for r in rs
            ]
        }

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        d = state["store"].run_detail(run_id)
        if d is None:
            raise HTTPException(404, "run not found")
        return d

    @app.post("/v1/runs/{run_id}/decide")
    def decide(run_id: str, body: DecideBody) -> dict:
        # Look up the matching DetectedField for this run.
        from sqlmodel import select
        store = state["store"]
        with store.session() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise HTTPException(404, "run not found")
            steps = list(s.exec(select(FormStep).where(FormStep.run_id == run_id)))
            df = None
            for st in steps:
                df = s.exec(
                    select(DetectedField)
                    .where(DetectedField.step_id == st.id)
                    .where(DetectedField.field_id == body.field_id)
                ).first()
                if df is not None:
                    break
            if df is None:
                raise HTTPException(404, "field not found in run")
            # Capture the prior LLM classification (if any) BEFORE we
            # stamp the override — we need to know whether the operator
            # *confirmed* what the model said or *rejected* it. That
            # signal is what trains the calibration cache.
            prior = s.exec(
                select(Classification)
                .where(Classification.detected_field_id == df.id)
                .order_by(Classification.id.desc())
            ).first()
            confirmed_prior = (
                prior is not None
                and prior.section == body.section.value
                and prior.source != "operator"
            )

            # Stamp an operator-override classification + decision.
            s.add(Classification(
                detected_field_id=df.id, section=body.section.value,
                confidence=1.0, reasoning="operator override", source="operator",
            ))
            s.add(DecisionRow(
                detected_field_id=df.id, action=body.action, value=body.value,
                reason="operator override", reviewed_by_human=True,
            ))

            # v3: feed the calibration cache. The label is the field's
            # human-visible label; we record:
            #   confirms[+1] for the operator's section
            #   rejects[+1]  for the model's section, if it differed
            cal = state.get("calibration")
            if cal is not None:
                cal.record(df.label, body.section, confirmed=True)
                if prior is not None and not confirmed_prior:
                    try:
                        prior_section = ResumeSection(prior.section)
                    except ValueError:
                        prior_section = None
                    if prior_section is not None and prior_section != body.section:
                        cal.record(df.label, prior_section, confirmed=False)
            s.commit()
        return {"ok": True}

    @app.put("/v1/profile/{name}")
    def upsert_profile(name: str, body: ProfileBody) -> dict:
        p = state["store"].upsert_profile(
            name=name,
            sections={k.value: v for k, v in body.sections.items()},
            extra=body.extra,
        )
        return {"id": p.id, "name": p.name}

    @app.get("/v1/calibration")
    def calibration_dump() -> dict:
        """Read-only view into the calibration cache. Each entry is a
        (label, section, confirms, rejects, accuracy). Auditable."""
        cal = state.get("calibration")
        if cal is None:
            return {"items": []}
        items: list[dict] = []
        for label_h, by_section in cal._by_label.items():  # noqa: SLF001
            for section, stat in by_section.items():
                items.append({
                    "label_hash": label_h,
                    "label": stat.last_text,
                    "section": section.value,
                    "confirms": stat.confirms,
                    "rejects": stat.rejects,
                    "accuracy": stat.accuracy,
                })
        items.sort(key=lambda x: (-x["confirms"], x["label"]))
        return {"items": items}

    @app.get("/v1/profile/{name}")
    def get_profile(name: str) -> dict:
        p = state["store"].get_profile(name)
        if p is None:
            raise HTTPException(404, "profile not found")
        return {
            "id": p.id, "name": p.name,
            "sections": json.loads(p.sections_json),
            "extra": json.loads(p.extra_json),
        }

    @app.get("/", response_class=HTMLResponse)
    def root() -> str:
        return _ROOT_HTML

    return app


# Suppress unused-import warning for re-exports the linter doesn't see.
_ = (Application,)


_ROOT_HTML = """<!doctype html>
<html><body style="font-family: ui-monospace, monospace; max-width: 720px; margin: 40px auto;">
<h1>JobAgent · Review Console API</h1>
<p>Endpoints under <code>/v1/</code>. The dossier UI lives at
<a href="http://localhost:5173">localhost:5173</a>.</p>
</body></html>"""

# A module-level app instance so production servers can run
# `uvicorn jobagent.api:app`.
app = build_app()
