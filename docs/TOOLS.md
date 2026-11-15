# MCP tools

The Portfolio, Marketplace and Workspace processes expose typed tools through the official
Python MCP SDK. Each Streamable HTTP endpoint is `/mcp` and requires its own bearer token.
Stdio is available for a trusted local client that launches the process directly.
The [Portfolio MCP README](../mcp_servers/portfolio/README.md) has Docker and stdio install commands.

| Server | Default port | HTTP credential | Scope |
| --- | ---: | --- | --- |
| Portfolio | 8101 | `MCP_PORTFOLIO_TOKEN` | One configured persona’s local corpus |
| Marketplace | 8102 | `MCP_MARKETPLACE_TOKEN` | One configured synthetic GigSim account |
| Workspace | 8103 | `MCP_WORKSPACE_TOKEN` | One local mock calendar and draft mailbox |

Use distinct values for the three HTTP credentials. HTTP startup rejects the example
placeholder. The marketplace server authenticates separately to GigSim using
`GIGBID_SIM_TOKEN` or the selected persona's entry in `GIGSIM_API_TOKENS`. It never exposes that
token as a tool argument. Pass `--persona ai_ml`, `--persona fullstack`, or `--persona
data_analyst` to the portfolio or marketplace process, or use `MCP_PORTFOLIO_PERSONA` and
`MCP_MARKETPLACE_PERSONA`. Both default to `fullstack`. Portfolio `--corpus` accepts a
single-corpus directory in the same layout as `data/personas/fullstack`.

```bash
uv run --extra rag python -m mcp_servers.portfolio.server --transport http --host 127.0.0.1
uv run python -m mcp_servers.marketplace.server --transport http --host 127.0.0.1
uv run python -m mcp_servers.workspace.server --transport http --host 127.0.0.1
```

The marketplace needs a running, seeded GigSim instance. The Workspace mock uses SQLite for
calendar holds and email drafts. Use `--db` for a separate Workspace database. The default
`.cache/mcp-workspace.sqlite3` and `.cache/mcp-audit.sqlite3` files are ignored by Git.

## Tool contracts

Every tool declares an input and an output schema and an idempotent annotation. Reading a
tool twice may reflect newer source data. A write replay with the same request and key returns
the original receipt. Marketplace calls are annotated open-world because they cross a service
boundary. Portfolio and Workspace use the closed local corpus and database.

| Server | Tool | Access | Result |
| --- | --- | --- | --- |
| Portfolio | `search_evidence` | Read | Ranked evidence and scores, with `k` and metadata filters |
| Portfolio | `get_case_study` | Read | Case study text and source metadata by evidence or project ID |
| Portfolio | `list_skills` | Read | Skills recorded in the configured portfolio |
| Marketplace | `search_jobs` | Read | Page of open jobs and an optional next cursor |
| Marketplace | `get_job` | Read | One job and public client summary |
| Marketplace | `get_client_history` | Read | Public past contracts and client ratings |
| Marketplace | `get_connects_balance` | Read | Account balance and refill details |
| Marketplace | `quote_connects` | Read | Current cost, projected balance and policy version |
| Marketplace | `get_market_stats` | Read | Price quartiles by domain, level and payment type |
| Marketplace | `list_proposals` | Read | Configured account's drafts and submissions |
| Marketplace | `get_proposal` | Read | One account-owned proposal |
| Marketplace | `list_threads` | Read | Configured account's conversations |
| Marketplace | `list_messages` | Read | Messages for one account-owned conversation |
| Marketplace | `list_events` | Read | Account events after an event ID |
| Marketplace | `create_proposal_draft` | Write | Exact proposal payload and quote on preview, or a private draft receipt |
| Marketplace | `submit_proposal` | Destructive write | Current quote on preview, or a submitted proposal receipt and Connects spent |
| Marketplace | `send_message` | Destructive write | Exact message and balance on preview, or the sent message receipt |
| Workspace | `free_slots` | Read | Available weekday slots in the requested IANA time zone |
| Workspace | `create_tentative_hold` | Write | Hold payload on preview, or a private hold receipt |
| Workspace | `create_email_draft` | Write | Email payload on preview, or a stored private draft receipt |

`submit_proposal` spends Connects and `send_message` delivers to a simulated client. Those
effects are irreversible, so both set the destructive hint. Workspace holds and email drafts
stay in the mock database and trigger no invitations or email delivery. Every write accepts
`dry_run`, which defaults to `true`. Previews perform no mutations. Production callers should
explicitly invoke a write after the workflow's human approval and keep write tools out of
drafting nodes. Pass the approved Connects quote into proposal submission so the tool can
recheck the policy and cost before the first write. GigSim's submit still owns the final budget
check, and quote validation and submission are separate requests.

GigSim persists the idempotency key and original marketplace result. Workspace stores a key
fingerprint and payload fingerprint, rejects reuse with edited content, and rejects conflicting
holds in one transaction. The append-only MCP audit records attempted, succeeded and failed
write calls with a timestamp, tool and key fingerprint. It excludes raw proposal text, contacts,
messages, keys and bearer tokens. Auditing traces calls rather than marketplace effects, so a
replayed call still has an audit record. A service operator can choose a separate audit database
with `--audit-db`. Preview content and saved draft content should be treated as private data.

Portfolio also exposes `portfolio://case/{id}`, `portfolio://proposal/{id}` and
`portfolio://repo/{name}`, plus prompts for answering a screening question, proving a
requirement and drafting an opening. The resource IDs resolve within the selected corpus.

## External provider boundaries

The local servers use the simulated GigSim marketplace and local portfolio and workspace
data. In live mode the Marketplace server uses the official Upwork MCP server through its OAuth
sign-in (trusted as Upwork's own sanctioned integration; every write stays a preview the person
confirms). See [Upwork MCP](UPWORK_MCP.md). GitHub imports
would require a scoped read-only repository token. Neither external provider is required to run
the local servers, and the Workspace server calls no calendar or email provider.

## Verification

`tests/contract/test_mcp_contract.py` locks the tool names, input/output schemas and annotations
and checks that authenticated Streamable HTTP initialization succeeds. The integration tests in
`tests/integration/test_mcp_servers.py` exercise the official SDK's in-memory transport,
portfolio evidence and resources, durable mock workspace writes, and a simulator preview and
replayed submission.

## Tool granularity experiment

The same 40 read-only fixture tasks exercised the granular Marketplace server and a three-tool
grouped server through the official MCP client. Both profiles matched all 40 fixture outcomes.
The grouped server reduced MCP calls from 66 to 40 and structured response JSON from 32,546 to
29,448 UTF-8 bytes. The grouped server still makes the underlying GigSim reads. The call and
byte counts capture what crossed the MCP boundary with predetermined routing. The experiment
did not invoke a language model.

```bash
uv run python -m evals.runners.tool_granularity \
  --output evals/results/tool_granularity.report.json \
  --details evals/results/tool_granularity.cases.json
```

The cases in `evals/datasets/tool_granularity.jsonl` include a task instruction and a fixture
expectation. The detailed result preserves each exact routed tool call and its arguments.
