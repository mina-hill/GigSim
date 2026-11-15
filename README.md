# GigBid

A multi-agent freelancer copilot: LangGraph agents find freelance jobs, judge whether they are
worth bidding on, write proposals grounded in real portfolio evidence, and manage client
conversations. A human approves every proposal and message before it leaves the system.

> Status: under construction (AI-4016 Assignment 2). The demo GIF, live link, architecture
> diagram, results tables and trace links will be added as each part is built and measured.

## Layout

| Path | What lives there |
| --- | --- |
| `apps/web`, `apps/api` | Next.js + TypeScript UI, FastAPI backend |
| `agent/` | LangGraph state, hand-offs, graphs, nodes, prompts, memory, model routing (`config.yaml`) |
| `rag/` | Portfolio ingestion, retrieval variants, claim verification |
| `mcp_servers/` | Portfolio, Marketplace and Workspace MCP servers |
| `gigsim/` | GigSim, the marketplace simulator used for development and grading |
| `worker/` | Scheduled scouting and long agent runs |
| `data/` | Personas, policy pack, processed data (generated, git-ignored) |
| `evals/` | Dev dataset, evaluation runners, committed results |
| `docs/` | Architecture, ADRs, data card, tools, runbook, extension, AI usage |

## Quick start (development)

```bash
uv sync --all-extras                  # Python 3.12 + pinned dependencies
cp .env.example .env                  # then fill in keys
make gigsim                           # simulator on :8081
make api                              # API on :8000
make test
```

Run `make help` for every command.

The [local bid walkthrough](docs/RUNBOOK.md#review-a-bid-in-the-local-simulator) runs without
model credentials and saves pending approvals between CLI sessions.
