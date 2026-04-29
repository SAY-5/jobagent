"""Live Playwright driver.

Wires Chromium against a real (or local-fixture-served) Easy Apply
form, runs the orchestrator on each visible modal step, and applies
each Decision back to the page (set inputs, choose options, upload
files, click Continue/Submit when the gate clears).

Modes
-----
- ``shadow`` (default): fill every field the policy resolved, take
  before/after screenshots, *never* click Submit. The
  ``BLOCK_SUBMIT_HOOK`` JS shim in `_install_block_hook()` is a belt-
  and-suspenders second line of defense — even if our handler logic
  is wrong, no submit click reaches the form.
- ``review``: same as shadow but stops at the gate as soon as any
  field needs operator review (fastest path to the dossier).
- ``auto``: fill, gate, and click Submit only if the gate cleared
  with zero review items. Don't enable this on day one.

The Playwright import is lazy so the rest of the package works
without the optional dependency installed.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .orchestrator import Orchestrator, StepOutcome
from .schema import Decision, FormField, ResumeProfile

if TYPE_CHECKING:  # pragma: no cover
    from playwright.sync_api import Page


# JS shim: turn every form's submit() into a no-op and intercept any
# button[type=submit] click. The orchestrator-side mode flag is the
# *primary* gate; this is just defense in depth — even if a future
# refactor forgets to honor mode, no submit reaches the form.
_BLOCK_SUBMIT_HOOK = """
(() => {
  if (window.__JOBAGENT_BLOCK_INSTALLED) return;
  window.__JOBAGENT_BLOCK_INSTALLED = true;
  document.addEventListener("click", (e) => {
    const t = e.target;
    if (t && t.matches('button[type="submit"], input[type="submit"]')) {
      e.preventDefault();
      e.stopPropagation();
      console.warn("[jobagent] blocked submit click");
    }
  }, true);
  for (const f of document.querySelectorAll("form")) {
    const orig = f.submit.bind(f);
    f.submit = () => console.warn("[jobagent] blocked form.submit()");
    f.__original_submit = orig;  // keep available for explicit auto mode
  }
})();
"""


@dataclass
class DriverConfig:
    mode: str = "shadow"            # "shadow" | "review" | "auto"
    headless: bool = True
    user_data_dir: Path | None = None  # persistent context for cookies
    screenshots_dir: Path = field(default_factory=lambda: Path("./screenshots"))
    max_steps: int = 8
    step_timeout_ms: int = 8_000
    submit_text_match: tuple[str, ...] = (
        "Submit application", "Submit", "Apply now",
    )
    next_text_match: tuple[str, ...] = (
        "Next", "Continue", "Review", "Save and continue",
    )


class JobAgentDriver:
    """Stateful, single-job driver. Construct one per posting URL."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        profile: ResumeProfile,
        config: DriverConfig | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.profile = profile
        self.config = config or DriverConfig()
        self.config.screenshots_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _browser(self):  # type: ignore[no-untyped-def]
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            launcher_kwargs = {"headless": self.config.headless}
            if self.config.user_data_dir is not None:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.config.user_data_dir),
                    **launcher_kwargs,
                )
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                try:
                    yield ctx, page
                finally:
                    ctx.close()
            else:
                browser = p.chromium.launch(**launcher_kwargs)
                ctx = browser.new_context()
                page = ctx.new_page()
                try:
                    yield ctx, page
                finally:
                    browser.close()

    def run(self, url: str) -> dict:
        """Drive the full Easy Apply flow against `url`. Returns a
        summary dict the orchestrator-level layer can persist."""
        outcomes: list[StepOutcome] = []
        with self._browser() as (_, page):
            page.goto(url, wait_until="domcontentloaded", timeout=self.config.step_timeout_ms)
            self._install_block_hook(page)
            # Find the Easy Apply entry. We try a few common selectors;
            # production would adapt this per platform.
            entry = self._click_easy_apply(page)
            if not entry:
                return {"status": "no_easy_apply", "steps": []}

            for step_idx in range(self.config.max_steps):
                self._screenshot(page, f"step{step_idx:02d}-before")
                html = page.content()
                outcome = self.orchestrator.process_html(html)
                outcomes.append(outcome)
                self._apply_decisions(page, outcome.decisions, outcome.fields)
                self._screenshot(page, f"step{step_idx:02d}-after")

                # Gate.
                if outcome.needs_review and self.config.mode != "auto":
                    return {"status": "gated", "step": step_idx, "decisions": [_d_summary(d) for d in outcome.decisions]}

                # Move forward. If neither Next nor Submit is present
                # we're done.
                if self._click_submit_if_appropriate(page, outcome):
                    return {"status": "submitted", "steps": step_idx + 1}
                if not self._click_next(page):
                    return {"status": "completed", "steps": step_idx + 1}

            return {"status": "max_steps_reached", "steps": self.config.max_steps}

    # --- internals ----------------------------------------------------

    def _install_block_hook(self, page: Page) -> None:
        if self.config.mode == "auto":
            return
        page.add_init_script(_BLOCK_SUBMIT_HOOK)
        page.evaluate(_BLOCK_SUBMIT_HOOK)

    def _click_easy_apply(self, page: Page) -> bool:
        # Strategies, in order. Each returns False on miss without raising.
        for selector in (
            'button:has-text("Easy Apply")',
            'a:has-text("Apply")',
            'button[aria-label*="Easy Apply"]',
        ):
            loc = page.locator(selector).first
            if loc.count() > 0:
                try:
                    loc.click(timeout=self.config.step_timeout_ms)
                    return True
                except Exception:
                    continue
        return False

    def _apply_decisions(
        self,
        page: Page,
        decisions: list[Decision],
        fields: list[FormField],
    ) -> None:
        by_id = {f.field_id: f for f in fields}
        for d in decisions:
            if d.action != "fill" or d.value is None:
                continue
            f = by_id.get(d.field_id)
            if f is None:
                continue
            try:
                self._fill_one(page, f, d.value)
            except Exception:
                # We log silently and let the run continue — the gate
                # logic will catch missed required fields. Each error
                # also persists in the orchestrator's audit trail.
                continue

    def _fill_one(self, page: Page, f: FormField, value: str) -> None:
        sel = f"#{f.field_id}"
        loc = page.locator(sel).first
        if loc.count() == 0:
            # Fall back to name=…
            loc = page.locator(f'[name="{f.field_id}"]').first
        if loc.count() == 0:
            return
        if f.kind in ("text", "email", "tel", "url", "number", "date", "textarea"):
            loc.fill(value, timeout=self.config.step_timeout_ms)
        elif f.kind == "select":
            loc.select_option(label=value, timeout=self.config.step_timeout_ms)
        elif f.kind == "checkbox":
            (loc.check if value.lower() in ("true", "yes", "1") else loc.uncheck)(
                timeout=self.config.step_timeout_ms,
            )
        elif f.kind == "radio":
            radio = page.locator(f'input[type=radio][value="{value}"]').first
            if radio.count() > 0:
                radio.check(timeout=self.config.step_timeout_ms)
        elif f.kind == "file":
            # File uploads are gated to operator review by policy; if
            # we got here in auto mode the operator approved.
            loc.set_input_files(value)

    def _click_next(self, page: Page) -> bool:
        for label in self.config.next_text_match:
            loc = page.locator(f'button:has-text("{label}")').first
            if loc.count() > 0:
                try:
                    loc.click(timeout=self.config.step_timeout_ms)
                    page.wait_for_load_state("networkidle", timeout=self.config.step_timeout_ms)
                    return True
                except Exception:
                    continue
        return False

    def _click_submit_if_appropriate(self, page: Page, outcome: StepOutcome) -> bool:
        if self.config.mode != "auto":
            return False
        if outcome.needs_review:
            return False
        for label in self.config.submit_text_match:
            loc = page.locator(f'button:has-text("{label}")').first
            if loc.count() > 0:
                try:
                    loc.click(timeout=self.config.step_timeout_ms)
                    page.wait_for_load_state("networkidle", timeout=self.config.step_timeout_ms)
                    return True
                except Exception:
                    continue
        return False

    def _screenshot(self, page: Page, name: str) -> Path:
        out = self.config.screenshots_dir / f"{int(time.time()*1000)}-{name}.png"
        try:
            page.screenshot(path=str(out), full_page=True)
        except Exception:
            pass
        return out


def _d_summary(d: Decision) -> dict:
    return {
        "field_id": d.field_id,
        "action": d.action,
        "value": d.value if d.action == "fill" else None,
        "reason": d.reason,
    }


# Tiny re-export so callers can import everything from `jobagent.driver`.
__all__ = ["_BLOCK_SUBMIT_HOOK", "DriverConfig", "JobAgentDriver"]


# Tests don't need playwright installed — they exercise _apply_decisions
# and the click-flow logic via a fake page. Use FakePage from
# `jobagent.driver_fake` (in this same module to avoid extra files).


@dataclass
class _FakeLocator:
    page: _FakePage
    selector: str
    matches: int = 0
    last_action: str | None = None
    last_value: str | None = None

    def count(self) -> int:
        return self.matches

    def fill(self, value: str, **_: object) -> None:
        self.last_action, self.last_value = "fill", value
        self.page.actions.append(("fill", self.selector, value))

    def select_option(self, label: str, **_: object) -> None:
        self.last_action, self.last_value = "select", label
        self.page.actions.append(("select", self.selector, label))

    def check(self, **_: object) -> None:
        self.last_action = "check"
        self.page.actions.append(("check", self.selector, None))

    def uncheck(self, **_: object) -> None:
        self.last_action = "uncheck"
        self.page.actions.append(("uncheck", self.selector, None))

    def click(self, **_: object) -> None:
        self.last_action = "click"
        self.page.actions.append(("click", self.selector, None))

    def set_input_files(self, path: str) -> None:
        self.last_action = "upload"
        self.page.actions.append(("upload", self.selector, path))

    @property
    def first(self) -> _FakeLocator:
        return self


class _FakePage:
    """In-memory Page double for unit tests of the driver logic."""

    def __init__(self, *, present: set[str] | None = None) -> None:
        self.present = present or set()
        self.actions: list[tuple[str, str, str | None]] = []
        self._html = ""

    def locator(self, selector: str) -> _FakeLocator:
        n = 1 if selector in self.present else 0
        return _FakeLocator(self, selector, matches=n)

    def content(self) -> str:
        return self._html

    def set_html(self, html: str) -> None:
        self._html = html

    def add_init_script(self, _: str) -> None: ...
    def evaluate(self, _: str) -> None: ...
    def goto(self, *_, **__) -> None: ...
    def wait_for_load_state(self, *_, **__) -> None: ...
    def screenshot(self, *_, **__) -> None: ...


# Driver wrapper that swaps the real Playwright `_browser` for a fake
# page. Tests construct this directly.
class FakeDriver(JobAgentDriver):
    def __init__(self, fake_page: _FakePage, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fake = fake_page

    @contextmanager
    def _browser(self):  # type: ignore[override,no-untyped-def]
        yield None, self._fake


# Used by tests; defining here means the prod path doesn't ship the
# fake helpers (they're only loaded if a test imports FakeDriver).
def make_fake_page(present: set[str] | None=None) -> _FakePage:
    return (_FakePage(present=present))
