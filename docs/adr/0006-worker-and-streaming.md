# ADR 0006: Runs execute in an Arq worker; the API streams their events over SSE

**Status:** accepted · 2026-11-14

**Decision.** Graph runs execute in an Arq worker (async, Redis-backed, cron built in). Each run writes its LangGraph stream events to a Redis Stream `run:{thread_id}`; the API relays them as Server-Sent Events and resumes from `Last-Event-ID`.

**Why.** Runs outlive web requests (approvals can wait days); SSE is one-way, simple and reconnects on its own.

**Consequences.** Approvals are read from the checkpointer, never from browser state, so a refresh or a restart never loses one.
