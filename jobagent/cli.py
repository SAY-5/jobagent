"""`jobagent` CLI entry point. Built on Typer for nice --help."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .orchestrator import Orchestrator
from .schema import ResumeProfile, ResumeSection
from .store import Store

app = typer.Typer(help="JobAgent — LinkedIn Easy Apply pipeline.")
console = Console()


def _store() -> Store:
    return Store(os.environ.get("JOBAGENT_DSN", "sqlite:///./jobagent.db"))


def _load_profile_file(path: Path) -> ResumeProfile:
    data = json.loads(path.read_text())
    return ResumeProfile(
        name=data["name"],
        sections={ResumeSection(k): v for k, v in data["sections"].items()},
        extra=data.get("extra", {}),
    )


@app.command()
def profile_set(name: str, path: Path) -> None:
    """Save a JSON resume profile to the local DB."""
    p = _load_profile_file(path)
    s = _store()
    s.upsert_profile(name=name, sections={k.value: v for k, v in p.sections.items()}, extra=p.extra)
    console.print(f"[green]✔[/green] profile {name!r} saved")


@app.command()
def runs(limit: int = 25) -> None:
    """List recent runs."""
    s = _store()
    rs = s.list_runs(limit=limit)
    t = Table(show_header=True)
    for col in ("id", "started_at", "status", "mode", "job_url"):
        t.add_column(col)
    for r in rs:
        t.add_row(
            r.id, r.started_at.isoformat(timespec="seconds"),
            r.status, r.mode, (r.job_url or "")[:60],
        )
    console.print(t)


@app.command()
def run_detail(run_id: str) -> None:
    """Show step-by-step detail for one run."""
    s = _store()
    d = s.run_detail(run_id)
    if d is None:
        raise typer.Exit("run not found")
    console.print_json(json.dumps(d, default=str))


@app.command()
def replay(html_path: Path, profile: str, mode: str = "shadow") -> None:
    """Run the pipeline against a saved HTML snapshot. Useful for tests
    and for replaying old runs against an updated classifier."""
    s = _store()
    p = s.get_profile(profile)
    if p is None:
        raise typer.Exit(f"profile {profile!r} not found")
    rp = ResumeProfile(
        name=p.name,
        sections={ResumeSection(k): v for k, v in json.loads(p.sections_json).items()},
        extra=json.loads(p.extra_json),
    )
    run = s.start_run(profile_id=p.id, job_url=str(html_path), mode=mode)
    orch = Orchestrator(store=s, profile=rp, run_id=run.id)
    outcome = orch.process_html(html_path.read_text())
    s.finish_run(run.id, "gated" if outcome.needs_review else "submitted")
    console.print_json(json.dumps(orch.serialize_outcome(outcome), default=str))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8090) -> None:
    """Start the review-console FastAPI server."""
    import uvicorn

    uvicorn.run("jobagent.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
