from __future__ import annotations

import pytest

from jobagent.driver import (
    _BLOCK_SUBMIT_HOOK,
    DriverConfig,
    FakeDriver,
    JobAgentDriver,
    make_fake_page,
)
from jobagent.orchestrator import Orchestrator
from jobagent.schema import ResumeProfile, ResumeSection
from jobagent.store import fresh_memory_store


@pytest.fixture()
def profile() -> ResumeProfile:
    return ResumeProfile(
        name="me",
        sections={
            ResumeSection.FIRST_NAME: "Sai",
            ResumeSection.LAST_NAME:  "Yamani",
            ResumeSection.EMAIL:      "me@example.com",
            ResumeSection.AUTHORIZED_TO_WORK: "Yes",
        },
    )


def _orchestrator(profile, store):
    pid = store.upsert_profile(
        name="me",
        sections={k.value: v for k, v in profile.sections.items()},
    ).id
    run = store.start_run(profile_id=pid, job_url="file://x", mode="shadow")
    return Orchestrator(store=store, profile=profile, run_id=run.id), run.id


def test_apply_decisions_fills_text_inputs(tmp_path, profile):
    store = fresh_memory_store()
    orch, _ = _orchestrator(profile, store)
    page = make_fake_page(present={"#firstName", "#email", "#auth"})
    cfg = DriverConfig(headless=True, screenshots_dir=tmp_path)
    drv = FakeDriver(page, orchestrator=orch, profile=profile, config=cfg)

    page.set_html(
        "<form>"
        '<label for="firstName">First Name</label>'
        '<input id="firstName" name="firstName" type="text" required>'
        '<label for="email">Email</label>'
        '<input id="email" name="email" type="email" required>'
        '<label for="auth">Authorized to work?</label>'
        '<select id="auth" required><option>Yes</option><option>No</option></select>'
        "</form>"
    )
    outcome = orch.process_html(page.content())
    drv._apply_decisions(page, outcome.decisions, outcome.fields)

    actions = {(a, sel): val for a, sel, val in page.actions}
    assert actions[("fill", "#firstName")] == "Sai"
    assert actions[("fill", "#email")] == "me@example.com"
    # Select picks the option label (fuzzy-matched against the field's options).
    assert actions[("select", "#auth")] == "Yes"


def test_shadow_mode_does_not_click_submit(tmp_path, profile):
    store = fresh_memory_store()
    orch, _ = _orchestrator(profile, store)
    page = make_fake_page(present={'button:has-text("Submit")', '#firstName'})
    drv = FakeDriver(page, orchestrator=orch, profile=profile,
                     config=DriverConfig(mode="shadow", screenshots_dir=tmp_path))
    page.set_html(
        '<form><label for="firstName">First Name</label>'
        '<input id="firstName" type="text"></form>'
    )
    outcome = orch.process_html(page.content())
    submitted = drv._click_submit_if_appropriate(page, outcome)
    assert submitted is False
    assert all(a[0] != "click" for a in page.actions), \
        "shadow mode must never click Submit"


def test_review_mode_returns_gated_when_review_needed(tmp_path, profile):
    store = fresh_memory_store()
    orch, _ = _orchestrator(profile, store)
    page = make_fake_page(present={'button:has-text("Easy Apply")', "#essay"})
    drv = FakeDriver(page, orchestrator=orch, profile=profile,
                     config=DriverConfig(mode="review", screenshots_dir=tmp_path))
    # Form has a free-form essay field that the LLM mock will leave UNMAPPED.
    page.set_html(
        '<form><label for="essay">Why are you a great fit for this role?</label>'
        '<textarea id="essay" required></textarea></form>'
    )
    summary = drv.run("file:///x")
    assert summary["status"] == "gated"
    # The essay field's decision should be "review".
    decisions = {d["field_id"]: d for d in summary["decisions"]}
    assert decisions["essay"]["action"] == "review"


def test_block_submit_hook_is_idempotent_js():
    """The JS shim must guard against double-install — pages can call it
    on every navigation."""
    # Asserting the source contains the idempotency check is enough; an
    # actual JS test would need a browser.
    assert "__JOBAGENT_BLOCK_INSTALLED" in _BLOCK_SUBMIT_HOOK
    assert "preventDefault()" in _BLOCK_SUBMIT_HOOK
    assert "stopPropagation()" in _BLOCK_SUBMIT_HOOK


def test_driver_screenshot_dir_is_created(tmp_path, profile):
    store = fresh_memory_store()
    orch, _ = _orchestrator(profile, store)
    target = tmp_path / "shots-nested"
    cfg = DriverConfig(headless=True, screenshots_dir=target)
    JobAgentDriver(orchestrator=orch, profile=profile, config=cfg)
    assert target.exists() and target.is_dir()


def test_run_returns_no_easy_apply_when_button_missing(tmp_path, profile):
    store = fresh_memory_store()
    orch, _ = _orchestrator(profile, store)
    page = make_fake_page(present=set())
    drv = FakeDriver(page, orchestrator=orch, profile=profile,
                     config=DriverConfig(mode="shadow", screenshots_dir=tmp_path))
    summary = drv.run("file:///x")
    assert summary == {"status": "no_easy_apply", "steps": []}


def test_fill_one_handles_radio_against_real_input(tmp_path, profile):
    """Radio inputs are matched by value, not by ID."""
    store = fresh_memory_store()
    orch, _ = _orchestrator(profile, store)
    page = make_fake_page(present={'#auth_yes', '[name="auth"]', 'input[type=radio][value="Yes"]'})
    cfg = DriverConfig(headless=True, screenshots_dir=tmp_path)
    drv = FakeDriver(page, orchestrator=orch, profile=profile, config=cfg)

    from jobagent.schema import FormField
    f = FormField(field_id="auth", label="Authorized?", kind="radio", required=True,
                  options=["Yes", "No"])
    drv._fill_one(page, f, "Yes")
    assert any(a == ("check", 'input[type=radio][value="Yes"]', None) for a in page.actions)
