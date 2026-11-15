# Runbook

## Start everything locally

```bash
cp .env.example .env            # fill in keys
docker compose -f infra/docker-compose.yml --env-file .env up --build
```

Services: web :3000, API :8000, GigSim :8081, MCP servers :8101 (portfolio), :8102 (marketplace), :8103 (workspace), Postgres :5432, Redis :6379, Ollama :11434.

Without Docker: `make gigsim`, `make api` and `make worker` in separate terminals.

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
