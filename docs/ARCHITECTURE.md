# Architecture

(Diagram and agent descriptions are filled in as Phases 2 to 5 land.)

- `gigsim/`: the marketplace simulator (separate service, see `gigsim/README.md` and ADR 0001).
- `mcp_servers/`: Portfolio, Marketplace and Workspace MCP servers; the marketplace port and its adapters (ADR 0004).
- `agent/`: model routing (ADR 0003), prompts, hand-offs, graphs, memory.
- `rag/`: ingestion, retrieval variants, claim verification.
- `apps/api`, `worker/`, `apps/web`: the product around the agents (ADR 0006).
