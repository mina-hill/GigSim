# Runbook

## Start everything locally

```bash
cp .env.example .env            # fill in keys
docker compose -f infra/docker-compose.yml --env-file .env up --build
```

Services: web :3000, API :8000, GigSim :8081, MCP servers :8101 (portfolio), :8102 (marketplace), :8103 (workspace), Postgres :5432, Redis :6379, Ollama :11434.

Without Docker: `make gigsim`, `make api` and `make worker` in separate terminals.

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

This path uses deterministic synthetic portfolio evidence, the local GigSim adapter and a
SQLite checkpointer. Production MCP transport, Postgres checkpoints and the web approval
screen remain separate milestones.

## Seed GigSim

```bash
make world      # builds data/processed/gigsim_world from processed Kaggle data + personas
make seed       # loads it into GigSim
```

## Live (Upwork) mode schedule

Live mode reads Upwork through its official MCP server every 180 minutes with ±15 minutes of jitter. Upwork's connected agents share one rate limit, and Upwork advises pausing scheduled searches when requests are refused, so on a 429 the worker backs off and doubles the interval (up to 12 hours). New postings rarely need minute-level reaction for a single freelancer. Writes are drafts only, confirmed by the freelancer on Upwork.

## Degraded mode

When an MCP server or GigSim is unreachable, the circuit breaker opens, `/health/deps` reports it, the UI shows a banner, and the worker re-queues work with a delay. Rehearse: `docker compose stop mcp-marketplace`, watch the banner, `docker compose start mcp-marketplace`, watch the queue drain.

## Crash demos (exactly-once submission)

See the "Durability" section of `docs/ARCHITECTURE.md`; the scripted versions are `scripts/demo_crash_resume.py`.
