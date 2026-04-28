# JobAgent — Deployment & Operations

JobAgent runs as a single FastAPI process plus a Playwright-driven CLI
tool. The API serves the dossier review console; the CLI launches
runs against live job postings.

## Quickstart (review console only, no browser)

```bash
pip install -e ".[dev]"
playwright install chromium
jobagent profile-set me ./examples/profile.json
jobagent serve            # http://127.0.0.1:8090
# Open the dossier UI by opening web/index.html — or run a tiny static server:
python -m http.server 5173 --directory web
```

Replay a saved HTML snapshot through the full pipeline:

```bash
jobagent replay tests/fixtures/easy_apply_step1.html me
jobagent runs
jobagent run-detail <run_id>
```

## Quickstart with Docker

```bash
docker compose up -d --build
# API:    http://localhost:8090
# Static console assets are mounted from ./web; serve them with any static
# server, or set up a reverse proxy that fronts FastAPI + the static dir.
```

## Configuration

| env var                  | default                              | meaning |
|--------------------------|--------------------------------------|---------|
| `JOBAGENT_DSN`           | `sqlite:///./jobagent.db`            | SQLAlchemy URL |
| `JOBAGENT_OPENAI_MODEL`  | `gpt-4o-mini`                        | structured-output model |
| `OPENAI_API_KEY`         | unset                                | required for the LLM classifier |
| `JOBAGENT_CORS`          | `http://localhost:5173`              | dossier-console origin |
| `BLOCK_SUBMIT`           | unset                                | when set, intercepts submit clicks (belt-and-suspenders for shadow mode) |

## Modes

- **shadow** (default) — fill the form, take a screenshot, never click
  Submit. Recommended for learning the agent's behavior before
  enabling auto-submit.
- **review** — fill what's auto-fillable, mark anything below
  `AUTO_CONFIDENCE` as needing review, halt at the gate.
- **auto** — same as review, but if no field needs review and all
  required fields are filled, click Submit. **Don't enable this on day
  one.** Spend a week in shadow mode first.

## Operational principles

- **Audit everything.** Each run persists the detected fields, the
  classifier output (with reasoning + source layer), the operator's
  edits if any, and screenshots before and after each step. Reviewing
  what the agent did wrong is the entire point of building it
  yourself; treat the audit log as the primary artifact.
- **Don't trust confidence in a vacuum.** A 0.92-confident
  classification on a label like "Are you authorized to work?" is
  rock-solid; a 0.92 on "How do you handle conflict?" is wishful
  thinking. The policy ladders confidence × required × kind for that
  reason.
- **Keep humans in the loop on first contact.** Default `AUTO_CONFIDENCE`
  is 0.85 *and* required fields just at the threshold round to review.
  You will be tempted to crank this down. Don't, until you've reviewed
  ~30 runs and seen the failure modes.
- **Respect rate limits and ToS.** This is a tool for your own job
  search. Running it at scale against LinkedIn is both bad citizenship
  and bad engineering — they'll serve you a CAPTCHA and the agent will
  silently start submitting blanks.

## Troubleshooting

| Symptom | Most likely cause | Fix |
|---------|-------------------|-----|
| `playwright install` hangs | Network or proxy issue | `PLAYWRIGHT_BROWSERS_PATH` to a writable location, retry |
| Every field comes back UNMAPPED | OpenAI key missing or wrong model | check `OPENAI_API_KEY`, set `JOBAGENT_OPENAI_MODEL` to a structured-output-capable model |
| Same field re-classified differently across runs | Cache invalidated by label drift | inspect `field_cache_key`; the label hash changes on punctuation, which is intentional |
| `sqlite database is locked` | Multiple processes hitting the same file | switch to Postgres via `JOBAGENT_DSN` |
| Submit clicks happen in shadow mode | Browser nav redirect, or page swallows preventDefault | set `BLOCK_SUBMIT=1`; the in-page hook catches the rest |
