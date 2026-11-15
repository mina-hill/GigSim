# Runbook

## Start everything locally

For the browser demo, install Node.js and install the pinned Python environment once.
Run these from the repository root on Windows, macOS, or Linux:

```bash
uv sync --locked --all-extras
uv run python -m scripts.local_demo
```

Open [the local workspace](http://127.0.0.1:3010). The launcher uses the bundled mini-world and
keeps the simulator and approvals in `.cache/local-demo`. Its three listeners bind to loopback.
Use `--database` with a new path for a fresh demo. Ctrl+C stops the services.

For an authenticated local workspace, run `uv run python -m scripts.local_demo --auth`
and open [port 3011](http://127.0.0.1:3011). The launcher puts accounts, approvals, settings
and simulator data in its separate `.cache/local-auth` folder. Each user signs up with a
username and password and claims one of the three available seeded personas. New workspace
preferences, including Markdown or text portfolio evidence, are in **Settings**. The job
viewer displays live steps and can export the saved timeline as a sanitized JSON trace.

### Docker Compose

Copy the environment template, then set distinct local values for `MCP_PORTFOLIO_TOKEN`,
`MCP_MARKETPLACE_TOKEN` and `MCP_WORKSPACE_TOKEN` before starting the HTTP servers.

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml --env-file .env up --build
```

The web UI listens on port 3000, API on 8000, GigSim on 8081, and the MCP servers on
8101–8103. Compose keeps GigSim, Redis and PostgreSQL data in named volumes. Set
`GIGBID_SCOUT_ENABLED=true` to enable the scout worker. It checks up to three unseen jobs per
persona at the saved search interval. Use the inbox search picker to run a saved filter manually.
Add `--profile models` before `up` to start Ollama on 11434. The synthetic reviewer flow works
with the offline profile.

## Review a bid in the local simulator

Install the locked environment with `uv sync --locked --all-extras`. Seed a fresh local GigSim
database with the mini-world fixture, then start the simulator.

```bash
uv run python -m gigsim.seed --world gigsim/fixtures/mini_world
uv run uvicorn gigsim.app:app --port 8081
```

In a separate terminal, set `GIGBID_SIM_TOKEN=dev-token-web` (PowerShell:
`$env:GIGBID_SIM_TOKEN = "dev-token-web"`) and run:

```bash
uv run python -m scripts.bid_demo job_web_stripe --persona fullstack
```

The demo prints the exact proposal and Connects quote. Choose approve, edit, reject or save and
exit. After an edit passes verification and compliance, review the revised preview and approve
it explicitly. Re-run the same command to reopen a pending approval. Use `--retry` to continue
a run interrupted during a marketplace call. Use a separate checkpoint file with
`--checkpoint` for each fresh simulator database and one reviewer per checkpoint file.

The CLI path uses deterministic synthetic portfolio evidence, the GigSim adapter and SQLite.

## Review bids in the browser

Install the locked Python dependencies and seed the synthetic fixture.

```bash
uv sync --locked --all-extras
uv run python -m gigsim.seed --world gigsim/fixtures/mini_world
uv run uvicorn gigsim.app:app --port 8081
```

Start the API in a second terminal and the Next.js app in a third.

```bash
uv run uvicorn apps.api.main:app --port 8000
```

```bash
cd apps/web
npm ci
npm run dev
```

Open `http://127.0.0.1:3000` and choose the full-stack persona. Open **Add Stripe
subscriptions to Next.js SaaS** to analyse the job and review the resulting draft. The browser
can reload a pending approval from `GIGBID_WEB_CHECKPOINT`, which defaults to
`.cache/gigbid-web.sqlite`. Use a fresh checkpoint when you reset the simulator world.

In **Messages**, select a client thread and use **Draft reply** on an unanswered message. Review
the reply and any interview options. An edit passes through the policy checks before an updated
draft appears. An explicit approval sends the displayed text. Rejection records a reason. See
[the timed demo](DEMO_SCRIPT.md) for a fixture-based conversation.

To regenerate the web client's schema after an API change, run from the repository root:

```bash
uv run python -m apps.api.export_openapi --output apps/web/openapi.json
npm --prefix apps/web run gen:api
```

## Seed GigSim

```bash
make world      # builds data/processed/gigsim_world from processed Kaggle data + personas
make seed       # loads it into GigSim
```

## Live (Upwork) mode schedule

The [Upwork MCP discovery command](UPWORK_MCP.md) supports a local OAuth callback. A real
account must authorize it before we can verify the tool schemas and map the live adapter.
The planned Upwork adapter uses a 180-minute polling interval with jitter and rate-limit
backoff to conserve shared limits. This demo uses synthetic accounts and GigSim.

## Degraded mode

The local browser checks GigSim through `/health/deps` and shows a banner when it is unreachable.
In the Compose demo, stop `gigsim` to see the banner and start it to recover. A saved run that
stopped mid-step can be resumed using its existing checkpoint after the dependency recovers.

## Crash demos (exactly-once submission)

Start a proposal, wait for the approval preview, restart the API, then reload the preview and
approve it. Check the Pipeline and confirm one proposal for the job. The integration and
reviewer-container tests also cover recovery with persisted state.
