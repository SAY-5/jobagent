"""SQLModel-backed persistence for runs, decisions, screenshots."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Session, SQLModel, create_engine, select


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(UTC)


class Profile(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("p"), primary_key=True)
    name: str
    sections_json: str
    extra_json: str = "{}"
    created_at: datetime = Field(default_factory=_now)


class Run(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("r"), primary_key=True)
    profile_id: str = Field(index=True, foreign_key="profile.id")
    job_url: str
    company: str | None = None
    title: str | None = None
    mode: str = "shadow"  # "shadow" | "auto" | "review"
    status: str = "in_progress"  # in_progress | gated | submitted | failed
    started_at: datetime = Field(default_factory=_now, index=True)
    finished_at: datetime | None = None
    notes: str | None = None


class FormStep(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("s"), primary_key=True)
    run_id: str = Field(index=True, foreign_key="run.id")
    idx: int
    html_hash: str
    screenshot_path: str | None = None
    captured_at: datetime = Field(default_factory=_now)


class DetectedField(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("f"), primary_key=True)
    step_id: str = Field(index=True, foreign_key="formstep.id")
    field_id: str
    label: str
    kind: str
    required: bool = False
    options_json: str = "[]"
    context: str | None = None


class Classification(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("c"), primary_key=True)
    detected_field_id: str = Field(index=True, foreign_key="detectedfield.id")
    section: str
    confidence: float
    reasoning: str
    source: str  # regex | cache | llm | operator


class DecisionRow(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("d"), primary_key=True)
    detected_field_id: str = Field(index=True, foreign_key="detectedfield.id")
    action: str  # fill | review | skip
    value: str | None = None
    reason: str
    reviewed_by_human: bool = False
    decided_at: datetime = Field(default_factory=_now)


class Application(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("a"), primary_key=True)
    run_id: str = Field(index=True, foreign_key="run.id")
    submitted_at: datetime | None = None
    confirmation_url: str | None = None


class Store:
    """Thin wrapper that hands out short-lived sessions."""

    def __init__(self, dsn: str = "sqlite:///./jobagent.db") -> None:
        connect_args: dict[str, Any] = {}
        engine_kwargs: dict[str, Any] = {"echo": False}
        if dsn.startswith("sqlite"):
            connect_args = {"check_same_thread": False}
            # In-memory SQLite needs StaticPool so all sessions share
            # one connection — otherwise each new session sees an
            # empty DB.
            if ":memory:" in dsn or dsn == "sqlite://":
                engine_kwargs["poolclass"] = StaticPool
        self.engine = create_engine(dsn, connect_args=connect_args, **engine_kwargs)
        SQLModel.metadata.create_all(self.engine)

    def session(self) -> Session:
        return Session(self.engine)

    # --- profile -------------------------------------------------------
    def upsert_profile(self, name: str, sections: dict[str, str],
                       extra: dict[str, str] | None = None) -> Profile:
        with self.session() as s:
            existing = s.exec(select(Profile).where(Profile.name == name)).first()
            if existing:
                existing.sections_json = json.dumps(sections)
                existing.extra_json = json.dumps(extra or {})
                s.add(existing)
                s.commit()
                s.refresh(existing)
                return existing
            p = Profile(
                name=name,
                sections_json=json.dumps(sections),
                extra_json=json.dumps(extra or {}),
            )
            s.add(p)
            s.commit()
            s.refresh(p)
            return p

    def get_profile(self, name: str) -> Profile | None:
        with self.session() as s:
            return s.exec(select(Profile).where(Profile.name == name)).first()

    # --- run -----------------------------------------------------------
    def start_run(self, profile_id: str, job_url: str, mode: str = "shadow") -> Run:
        r = Run(profile_id=profile_id, job_url=job_url, mode=mode)
        with self.session() as s:
            s.add(r)
            s.commit()
            s.refresh(r)
        return r

    def finish_run(self, run_id: str, status: str, notes: str | None = None) -> None:
        with self.session() as s:
            r = s.get(Run, run_id)
            if r is None:
                return
            r.status = status
            r.finished_at = _now()
            if notes:
                r.notes = notes
            s.add(r)
            s.commit()

    def list_runs(self, limit: int = 50) -> list[Run]:
        with self.session() as s:
            return list(s.exec(select(Run).order_by(Run.started_at.desc()).limit(limit)))

    def get_run(self, run_id: str) -> Run | None:
        with self.session() as s:
            return s.get(Run, run_id)

    # --- step / fields -------------------------------------------------
    def add_step(self, run_id: str, idx: int, html_hash: str,
                 screenshot_path: str | None = None) -> FormStep:
        st = FormStep(run_id=run_id, idx=idx, html_hash=html_hash,
                      screenshot_path=screenshot_path)
        with self.session() as s:
            s.add(st)
            s.commit()
            s.refresh(st)
        return st

    def add_field(self, step_id: str, field_id: str, label: str, kind: str,
                  required: bool, options: list[str], context: str | None = None) -> DetectedField:
        f = DetectedField(
            step_id=step_id, field_id=field_id, label=label, kind=kind,
            required=required, options_json=json.dumps(options), context=context,
        )
        with self.session() as s:
            s.add(f)
            s.commit()
            s.refresh(f)
        return f

    def add_classification(self, detected_field_id: str, section: str,
                           confidence: float, reasoning: str, source: str) -> Classification:
        c = Classification(
            detected_field_id=detected_field_id,
            section=section, confidence=confidence,
            reasoning=reasoning, source=source,
        )
        with self.session() as s:
            s.add(c)
            s.commit()
            s.refresh(c)
        return c

    def add_decision(self, detected_field_id: str, action: str, value: str | None,
                     reason: str, reviewed_by_human: bool = False) -> DecisionRow:
        d = DecisionRow(
            detected_field_id=detected_field_id, action=action, value=value,
            reason=reason, reviewed_by_human=reviewed_by_human,
        )
        with self.session() as s:
            s.add(d)
            s.commit()
            s.refresh(d)
        return d

    # --- run detail (for the review console) ---------------------------
    def run_detail(self, run_id: str) -> dict[str, Any] | None:
        with self.session() as s:
            run = s.get(Run, run_id)
            if run is None:
                return None
            steps = list(s.exec(select(FormStep).where(FormStep.run_id == run_id)
                                .order_by(FormStep.idx)))
            steps_out = []
            for st in steps:
                fields = list(s.exec(select(DetectedField).where(DetectedField.step_id == st.id)))
                f_out = []
                for f in fields:
                    # Decisions are uuid-keyed, so order by timestamp;
                    # ties (same wall-clock ms) fall back to id.desc()
                    # — operator overrides are stamped after the
                    # initial classification so they win in either case.
                    cls = s.exec(
                        select(Classification)
                        .where(Classification.detected_field_id == f.id)
                        .order_by(Classification.id.desc())
                    ).first()
                    dec = s.exec(
                        select(DecisionRow)
                        .where(DecisionRow.detected_field_id == f.id)
                        .order_by(DecisionRow.decided_at.desc(), DecisionRow.id.desc())
                    ).first()
                    f_out.append({
                        "id": f.id,
                        "label": f.label,
                        "kind": f.kind,
                        "required": f.required,
                        "options": json.loads(f.options_json),
                        "classification": {
                            "section": cls.section, "confidence": cls.confidence,
                            "reasoning": cls.reasoning, "source": cls.source,
                        } if cls else None,
                        "decision": {
                            "action": dec.action, "value": dec.value,
                            "reason": dec.reason, "reviewed": dec.reviewed_by_human,
                        } if dec else None,
                    })
                steps_out.append({
                    "idx": st.idx,
                    "html_hash": st.html_hash,
                    "screenshot": st.screenshot_path,
                    "fields": f_out,
                })
            return {
                "id": run.id, "job_url": run.job_url,
                "company": run.company, "title": run.title,
                "mode": run.mode, "status": run.status,
                "started_at": run.started_at.isoformat(),
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "steps": steps_out,
            }


# Re-export a tiny helper for tests that just want a fresh in-memory store.
def fresh_memory_store() -> Store:
    return Store("sqlite:///:memory:")


# (Used in seeders / replays; not part of the public API surface.)
_ = time
