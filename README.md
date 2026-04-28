# JobAgent

A Python agent that fills LinkedIn-style "Easy Apply" forms. Built
during my own job search, primarily as a learning artifact about the
failure modes of LLM-driven agents in noisy DOM environments.

> **Honest disclaimer.** This was never perfectly reliable, and that's
> the point. The interesting work was the structured classification
> layer that makes the model's decisions consistent enough to audit.
> Default mode is **shadow**: fill the form, screenshot, never submit.

## What's interesting in here

- **Closed-set classification with structured outputs.** Form labels
  → one of ~17 ResumeSection slots (or UNMAPPED). The OpenAI structured
  output API rejects anything outside the schema; that single
  constraint eliminates the entire class of "model hallucinated a field
  name that doesn't exist" failures.
- **Layered classifier.** Regex prefilter → cache (keyed on
  label_hash + options_hash) → LLM → operator override. The first two
  layers handle ~80% of LinkedIn's Easy Apply fields without any LLM
  call; latency and cost stay flat as form size grows.
- **Policy engine separated from classification.** Confidence × kind
  × required determines `fill / review / skip`. File uploads never
  auto-fill regardless of confidence. Required fields just at the
  threshold round to review.
- **Audit log as the primary artifact.** Every run saves detected
  fields, classifier output (with reasoning), operator edits if any,
  and screenshots before/after. Re-reading what went wrong is more
  useful than a green tick.

## Quick start

```bash
pip install -e ".[dev,openai]"
playwright install chromium
jobagent profile-set me ./examples/profile.json
jobagent replay tests/fixtures/easy_apply_step1.html me
jobagent serve
# Open ./web/index.html for the dossier review console.
```

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the design writeup
(structured layer, pipeline, persistence). See [`docs/DEPLOY.md`](
./docs/DEPLOY.md) for the operational checklist.

## The structured layer in 30 seconds

```python
class ResumeSection(StrEnum):
    FIRST_NAME = "first_name"
    EMAIL      = "email"
    # ... ~17 in total ...
    UNMAPPED   = "unmapped"        # always allowed; lets the model opt out

class FieldClassification(BaseModel):
    field_id:   str
    section:    ResumeSection      # closed enum
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning:  str = Field(..., max_length=400)
```

That's the contract. The OpenAI client gets this Pydantic model as
`text_format=`; the API rejects anything that doesn't fit. The
discriminated `UNMAPPED` variant means the model can opt out instead
of guessing — and it does.

## Tests

```bash
pytest -q
```

26 tests covering: schema round-trip + validation, regex prefilter
correctness, cache short-circuit + drift-resistance, operator
override, policy engine ladder (confidence × kind × required),
fixture-replay end-to-end (LinkedIn Easy Apply HTML snapshot in,
expected decisions out), API endpoints (list runs, run detail,
operator override propagation).

## Companion projects

- **[canvaslive](https://github.com/SAY-5/canvaslive)** — multiplayer OT whiteboard
- **[pluginforge](https://github.com/SAY-5/pluginforge)** — Web Worker plugin sandbox
- **[agentlab](https://github.com/SAY-5/agentlab)** — AI agent eval harness
- **[payflow](https://github.com/SAY-5/payflow)** — payments API
- **[queryflow](https://github.com/SAY-5/queryflow)** — natural-language SQL
- **[datachat](https://github.com/SAY-5/datachat)** — sandboxed Python data analysis
- **[distributedkv](https://github.com/SAY-5/distributedkv)** — sharded KV with Raft
- **jobagent** — you're here. LinkedIn Easy Apply agent.
- **[inferencegateway](https://github.com/SAY-5/inferencegateway)** — high-throughput LLM serving frontend
- **[ticketsearch](https://github.com/SAY-5/ticketsearch)** — event discovery + inventory platform
- **[netprobekit](https://github.com/SAY-5/netprobekit)** — hardware diagnostics framework
- **[releaseguard](https://github.com/SAY-5/releaseguard)** — CI/CD test infrastructure

## License

MIT.
