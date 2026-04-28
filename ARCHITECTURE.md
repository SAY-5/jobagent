# JobAgent — Architecture

> A Python agent that drives LinkedIn-style "Easy Apply" forms end-to-
> end. Playwright handles the browser; an LLM (OpenAI structured
> outputs by default) makes the field-mapping decisions; a structured
> classification layer constrains those decisions so they stay
> consistent across slightly varying form layouts.

## What this project is — and isn't

**It is** a learning artifact about the failure modes of LLM-driven
agents in noisy DOM environments. The README front-and-centers that
this is not a "fire-and-forget" submission machine; it's a tool I built
during my own search to automate the mechanical parts and to learn
where these systems break.

**It isn't** a tool for high-volume spam. The default policy requires
human review for any application below a confidence floor; a "shadow
mode" that fills the form but never clicks Submit is the recommended
way to use it.

## Top-level diagram

```
        ┌──────────────────────────────────────────┐
        │      operator (CLI · review console)     │
        └───────────┬─────────────────┬────────────┘
                    │                 │ http
                    │ launch run      ▼
                    │             ┌────────────────┐
                    │             │   review web   │
                    │             │   FastAPI +    │
                    │             │   React UI     │
                    │             └────────┬───────┘
                    ▼                      │
            ┌───────────────┐              │
            │   pipeline    │              │
            │   orchestrator│              ▼
            └─────┬─────────┘     ┌────────────────┐
                  │               │   sqlite store │
        ┌─────────┼─────────┐     │  runs/forms/   │
        ▼         ▼         ▼     │  decisions/    │
   ┌────────┐ ┌────────┐ ┌────────┐ │ applications  │
   │browser │ │classify│ │persist │ └────────────────┘
   │playwr. │ │OpenAI  │ │sqlite  │
   └────────┘ └────────┘ └────────┘
```

## Pipeline

For one job posting URL:

1. **discovery** — Playwright opens the page, waits for the Easy Apply
   button, clicks it. Modal appears.
2. **detection** — for each form step, walk the DOM and extract a
   structured `FormField` list: label text, input kind (text /
   textarea / select / radio / file / checkbox), id, options, required
   flag, surrounding context.
3. **classification** — for each `FormField`, ask the structured-
   output LLM to map it to one of N predefined `ResumeSection` slots
   (`first_name`, `years_of_experience`, `phone`, `cover_letter`, …).
   The LLM returns a discriminated union with a confidence score; an
   `UNMAPPED` variant is always available so the model can opt out
   instead of guessing.
4. **decision** — policy engine decides per field:
   - `confidence >= AUTO_FLOOR` and `type ∈ AUTO_TYPES` → auto-fill.
   - `confidence >= REVIEW_FLOOR` → fill, mark for review.
   - else → leave blank, raise to operator.
5. **fill** — Playwright sets values; for selects/radios, we look up
   the predicted option via fuzzy match against the actual options on
   the page (the LLM doesn't see the option list during classification,
   only the field's purpose).
6. **gate** — if any field needs review or any required field is
   blank, the run is paused. The operator opens the review console;
   approves/edits; resumes the run.
7. **submit (optional)** — if the run was started with `--auto-submit`
   and the gate passes, we click Submit. Default is `--shadow`: fill
   everything, never submit, save a screenshot for verification.
8. **persist** — every step writes a record: the raw HTML hash, the
   detected fields, the classifier output (with reasoning), the
   operator's edits if any, the final value, screenshots before/after.

The audit trail is the whole point. When the agent fails, we want to
read back exactly which decision was wrong and why.

## Structured classification layer

This is the part that actually makes the agent reliable. Naive prompt
"map these fields to my resume" produces unstable answers because:

- The model can pick fields from outside the allowed set.
- Confidence is implied by free text, not exposed numerically.
- Format drift between two near-identical pages produces different
  outputs.

The fix is a typed envelope. We define, in `jobagent/schema.py`:

```python
class ResumeSection(StrEnum):
    FIRST_NAME = "first_name"
    LAST_NAME  = "last_name"
    EMAIL      = "email"
    PHONE      = "phone"
    LOCATION   = "location"
    CURRENT_TITLE = "current_title"
    YEARS_EXPERIENCE = "years_experience"
    AUTHORIZED_TO_WORK = "authorized_to_work"
    REQUIRES_SPONSORSHIP = "requires_sponsorship"
    LINKEDIN_URL = "linkedin_url"
    PORTFOLIO_URL = "portfolio_url"
    GITHUB_URL = "github_url"
    RESUME_FILE = "resume_file"
    COVER_LETTER = "cover_letter"
    SALARY_EXPECTATION = "salary_expectation"
    AVAILABLE_START_DATE = "available_start_date"
    UNMAPPED = "unmapped"   # always allowed; lets the model opt out

class FieldClassification(BaseModel):
    field_id: str
    section: ResumeSection
    confidence: float          # 0..1, model's self-reported
    reasoning: str             # short, < 200 chars
```

We pass this schema to OpenAI's `responses.parse` (structured
outputs); the API rejects any output that doesn't match. That removes
two whole classes of failures (wrong field name; missing confidence)
without hand-coded validation.

For consistency under DOM drift we add a deterministic post-processor
that:
- Lowercases + collapses whitespace before hashing.
- Caches `(label_hash, options_hash) → classification` so the same
  field on a re-tried run gets the same answer.
- Falls back to a regex map for the high-confidence simple cases
  (e.g. `r"(?i)\bemail\b"` → `EMAIL`); the LLM is only consulted when
  the regex map doesn't fire.

The cache + regex prefilter cuts LLM calls by ~80% on the LinkedIn
sample fixtures and is the single biggest reliability win after
structured outputs.

## Browser automation

- Playwright sync API. Async would let us drive multiple jobs in
  parallel but the LinkedIn rate-limit makes that academic — one tab
  at a time is the right speed.
- Chromium with persistent context so logged-in cookies survive
  across runs.
- Every action goes through a `safe_click` / `safe_fill` wrapper that
  waits for the element + screenshots on failure.
- `BLOCK_SUBMIT` env var, when set, intercepts any `<button[type=submit]>`
  click in the page context and turns it into a no-op. Belt-and-
  suspenders for shadow mode.

## Review console

A small FastAPI + React app that shows:

- Recent runs (job title, company, status, confidence histogram)
- Per-run timeline: each form step, each field, the classifier output,
  the operator's edits if any, before/after screenshots
- Resume profile editor (the source of truth for ResumeSection values)
- Replay button: re-run a saved run with a fresh classifier (useful
  when iterating on prompts)

Aesthetic: a manila-folder dossier look — typewriter fonts, paper-tone
backgrounds, ink-stamp status badges, stitched section dividers. The
goal is to feel like reading a case file, not a SaaS dashboard.

## Persistence

SQLite via SQLModel. Five tables:

```
profile(id, name, json)
runs(id, profile_id, job_url, started_at, status, mode)
form_steps(id, run_id, idx, screenshot_path, html_hash)
detected_fields(id, step_id, field_id, label, kind, required, options_json)
classifications(id, field_id, section, confidence, reasoning, source)
decisions(id, field_id, action, value, reviewed_by_human)
applications(id, run_id, submitted_at, confirmation_url)
```

Migrations are alembic; the schema is small enough that a single
revision covers it.

## Tests

- **schema tests** — round-trip `FieldClassification` JSON; reject
  malformed inputs; the regex prefilter's expected outputs.
- **fixture replay** — saved HTML snapshots of LinkedIn Easy Apply
  modals (with credentials redacted); the detector + classifier run
  against the snapshot, the expected mapping is asserted. No live
  network in CI.
- **OpenAI mock** — a local provider that responds with deterministic
  classifications, so the rest of the stack tests offline.
- **policy engine** — table-driven: confidence × type × required →
  expected action.
- **playwright smoke** — boots Chromium against a tiny self-served
  HTML fixture (a stand-in Easy Apply form), exercises the full fill
  pipeline.

## Non-goals

- **Bypassing CAPTCHA, 2FA, or behavioral fingerprinting.** Out of
  scope and out of bounds.
- **Submission without human review for low-confidence runs.** The
  default policy holds at the gate; opting out requires an explicit
  CLI flag.
- **Cross-platform support beyond LinkedIn Easy Apply.** Greenhouse /
  Lever adapters are sketched in `jobagent/adapters/` but not wired
  in v0.1.
