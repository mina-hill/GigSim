# GigBid

A multi-agent freelancer copilot: LangGraph agents find freelance jobs, judge whether they are
worth bidding on, write proposals grounded in real portfolio evidence, and manage client
conversations. A human approves every proposal and message before it leaves the system.

> Status: under construction (AI-4016 Assignment 2). The local simulator review workflow and
> smoke evaluation are available. Hosted review and the comparative experiments are pending.

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

The [web walkthrough](docs/RUNBOOK.md#review-bids-in-the-browser) covers persona selection,
the job inbox, proposal approval and the simulator pipeline.

The [reviewer deployment guide](docs/DEPLOYMENT.md) packages the three services behind one
authenticated HTTPS ingress with persistent simulator state.

## Evaluation

<!-- BEGIN EVALUATION RESULTS -->

### Offline bid graph smoke

30 synthetic development jobs sampled by persona and job kind with seed 4016. Initial decisions are scored before drafting and watch counts as a non-bid. The graph stops at human approval. Claims are checked by the offline verifier and no proposals are sent.

| Metric | Result |
| --- | ---: |
| Correct bid decisions | 21 / 30 |
| Bid F1 | 0.7097 |
| Drafts reaching review | 14 / 30 |
| Claims flagged as unsupported at review | 0 / 28 |

[Result artifact](evals/results/bid-smoke.report.json)

Reproduce with <code>uv run python -m evals.runners.smoke --jobs 30 --gate</code>.

<!-- END EVALUATION RESULTS -->
