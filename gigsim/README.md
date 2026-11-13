# GigSim

A marketplace simulator that stands in for a real freelance platform during development and
grading. The assignment brief describes GigSim as provided; the starter kit did not include it,
so this repository builds its own. After the `gigsim-v1.0` tag it is treated as frozen: changes
ship as new versions (for example a new policy file), and CI checks that nothing else changed.

GigBid never imports this package. Agents reach GigSim only through the Marketplace MCP server
and the marketplace port, exactly as they would reach a live marketplace.

## Run

```bash
uv run uvicorn gigsim.app:app --port 8081          # API docs at http://localhost:8081/docs
uv run python -m gigsim.seed --world gigsim/fixtures/mini_world
```

## API

Freelancer endpoints take `Authorization: Bearer <token>` (tokens map to accounts through
`GIGSIM_API_TOKENS`). Every write takes an `Idempotency-Key` header.

| Method and path | Purpose |
| --- | --- |
| `GET /jobs?q=&domain=&payment_type=&min_budget=&posted_after=&limit=&cursor=` | Search open jobs released so far |
| `GET /jobs/{id}` | One job with client summary and current Connects cost |
| `GET /jobs/{id}/connects-quote` | Cost, balance and balance after applying (for dry runs) |
| `GET /clients/{id}/history` | Past contracts, reviews, spend, hire rate |
| `GET /market/stats?domain=&experience_level=&payment_type=` | P25, P50, P75 of budgets or hourly rates |
| `GET /me`, `GET /me/connects` | Account and Connects balance |
| `POST /proposals` | Create a draft |
| `GET /proposals`, `GET /proposals/{id}` | List or read your proposals and their pipeline stage |
| `POST /proposals/{id}/submit` | Submit and spend Connects |
| `GET /threads`, `GET /threads/{id}/messages`, `POST /threads/{id}/messages` | Client conversations |
| `GET /events?since=` | Submitted, viewed, client replied, interview, hired, lost, messages, refills |

Admin endpoints take `X-Admin-Token`:

| Method and path | Purpose |
| --- | --- |
| `POST /admin/seed {world_path}` | Reset the database and load a world folder |
| `POST /admin/tick {n}` | Advance simulated time (1 tick = 1 hour) and process decisions and replies |
| `GET/PUT /admin/faults` | Error rate, "response lost after commit" rate, latency, outage, rate limit |
| `PUT /admin/policy {version}` | Switch policy file (the change request ships as a new version) |
| `PUT /admin/accounts/{id}/connects {balance}` | Reset a balance between evaluation runs |
| `GET /admin/proposals/{id}/decision` | The simulated client's hidden reasoning (failure analysis only) |
| `GET /admin/threads/{id}/gold` | Gold intents of client messages (intent-classifier evaluation) |
| `GET /admin/state` | Tick, policy, counts, current faults |

## How simulated clients decide

Each client has hidden preferences (generic-opening dislike, preferred length, responsiveness,
price tolerance, strictness, scammer flag), and each job has hidden must-have skills and a price
ceiling. None of it is returned by the public API (a test checks this).

1. Hard rules: contact details or messaging apps in a proposal, or a bid above the ceiling,
   mean the proposal is ignored.
2. A score from observable features: must-have skills mentioned (35%), concrete evidence such
   as numbers and "I built" (20%), overlap with the job's own words (20%), length fit (10%),
   screening answers (10%), price position (5%); minus penalties for a generic opening,
   guarantee language, crowded jobs and strict clients; plus deterministic noise of ±0.04.
3. Score to outcome: under 0.35 ignored, under 0.58 viewed, under 0.72 replied, otherwise an
   interview request. Some clients go quiet (more often when less responsive).
4. In conversations the client asks a question, negotiates price or asks for interview slots;
   scam clients try to move the freelancer off-platform. Hiring is decided after the interview.

With `GIGSIM_CLIENT_MODE=llm`, step 2 is blended 50/50 with an LLM playing the client
(`prompts/client_decision.v1.md`), cached per proposal so reply rates stay reproducible.
All randomness derives from `GIGSIM_SEED` and ids, so the same run gives the same outcomes.

## World format

A world is a folder of JSONL files: `freelancers.jsonl`, `clients.jsonl`,
`past_contracts.jsonl`, `jobs.jsonl`, and optionally `market_stats.json`. See `seed.py` for the
schema of each record and `fixtures/mini_world/` for a small example. Jobs with
`status: closed` are history (used for market statistics); `release_tick` controls when an
open job appears in search, which is how the live job stream is simulated.
