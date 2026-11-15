# Upwork MCP desktop discovery

Upwork supports MCP clients that run on your computer and complete OAuth in the browser.
Its FAQ says desktop clients can use local callbacks. Custom web-hosted clients currently
need a callback that Upwork already approves. The GigBid discovery command opens a
loopback callback on an ephemeral port and uses the MCP Python SDK for OAuth authorization
code with PKCE and OAuth client registration. [Upwork MCP FAQ](https://www.upwork.com/ai/mcp?trk=public_profile__reactions-text) · [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)

On the same computer as the browser, run this command from the repository root. Sign in on
Upwork's own consent page and review the requested permissions.

```bash
uv run python -m mcp_servers.marketplace.adapters.upwork_discovery --output .cache/upwork-tools.json
```

The command uses `MultiServerMCPClient` over Streamable HTTP and collects paginated
`tools/list` metadata. It does not call any marketplace tools. The listener checks the
loopback host, callback path and OAuth state, accepts a single completion, and closes when the
command exits. The default deadline is 180 seconds, configurable using `--timeout`. OAuth
tokens and dynamic client credentials stay in memory for this one process. The optional
output file contains tool metadata only.

Upwork's plugin documents operations including `find_jobs` with `get` or `search`,
`get_profile`, `list_freelancer_proposals`, and a `manage_proposals` action that
prepares a preview. It directs clients to authenticated `search_tools` and
`get_tool_help` for role-specific actions and full parameter schemas. Top-level
`tools/list` may be in compact mode or expose only minimal schemas. A complete
live adapter requires those authoritative contracts before mapping request or response
fields onto GigBid's marketplace port. [Upwork's workflow guide](https://raw.githubusercontent.com/upwork/upwork-agent-plugin/master/skills/upwork-workflows/SKILL.md) · [Upwork's proposal workflow](https://raw.githubusercontent.com/upwork/upwork-agent-plugin/master/skills/write-proposal/SKILL.md)

Discovery keeps its credentials in memory and forgets them when it exits.

## Connect your account

The connect command signs a GigBid user in once and keeps the sign-in for later runs. Run it on
the computer with the browser, from the repository root:

```bash
uv run python -m mcp_servers.marketplace.adapters.upwork_connect connect
uv run python -m mcp_servers.marketplace.adapters.upwork_connect status
uv run python -m mcp_servers.marketplace.adapters.upwork_connect disconnect
```

`--user` names the GigBid user (default `local`). Each user's tokens, registered desktop client
and chosen Upwork account are stored in `.cache/upwork/<user>.oauth`, encrypted with the Fernet
key in `TOKEN_ENCRYPTION_KEY`. The command adds a key to `.env` when none is set; without that
key the file cannot be read. The callback listens on `127.0.0.1:8765` (`--port` changes it), so
the redirect URI registered with Upwork stays the same between runs.

Later runs open the session with the saved sign-in. An expired access token is refreshed at the
token endpoint saved from the authorization server's metadata. Servers and the worker never open
a browser: when the saved sign-in stops working they report that the user must connect again.

`connect` also saves Upwork's full reference for the tools GigBid maps (`get_tool_help`) and a
few small read-only samples in `.cache/upwork/reference.json`. The marketplace adapter's field
mapping is checked against that file. The command only reads; it creates, submits and sends
nothing.

## Live mode

`mcp_servers/marketplace/adapters/upwork_live.py` implements the same marketplace port as the
GigSim adapter, so the graph cannot tell which marketplace it uses. Set
`MARKETPLACE_MODE=upwork` and the Marketplace MCP server serves its tools from the saved
sign-in (`--upwork-user`, default `local`).

| Port method | Upwork tool and action |
| --- | --- |
| `search_jobs` | `find_jobs search` with a query, or `smart_search` (`most_recent`) without one |
| `get_job`, `quote_connects` | `find_jobs get`: description, contract terms, client record, `connects_cost` |
| `get_client_history` | `find_jobs get` on one of the client's jobs (search rows carry no client id) |
| `get_connects_balance` | `get_profile connects_balance` |
| `list_proposals`, `get_proposal` | `list_freelancer_proposals list` and `get` |
| `list_threads`, `list_messages` | `get_messages list_rooms` and `list_messages` |
| `create_proposal_draft` | `list_freelancer_proposals invitations`, then `manage_proposals create` |
| `get_market_stats`, `list_events` | none: Upwork publishes neither, so both return empty lists |

Writes stop at Upwork's preview. `create_proposal_draft` asks Upwork to prepare a proposal
preview, with the screening answers when the job has questions; Upwork stores it and nothing is
submitted or spent. `submit_proposal` never calls `confirm_preview`: it returns the preview id
and the command that confirms it. `send_message` is refused; the person sends approved replies
on Upwork. Upwork refuses cover letters over 5,000 characters, and an existing invitation for
the job stops the draft. The adapter makes one call at a time with at least one second between
calls. Job and message text arrives inside Upwork's `untrusted_participant_content` tags; the
adapter removes the tags, and GigBid's own prompts fence the text as untrusted.

```bash
uv run python -m mcp_servers.marketplace.adapters.upwork_connect check
uv run python -m mcp_servers.marketplace.adapters.upwork_connect confirm <preview_id>
```

`check` reads the account through the adapter and prints the Connects balance, the newest
matching jobs and one job's Connects cost. `confirm` shows Upwork's stored preview, including
the Connects it will spend, and calls `confirm_preview` only after the person types `SUBMIT`.

The local web app's workflow still runs on GigSim. Live mode is available through the
Marketplace MCP server and these commands.

```bash
uv run pytest -q tests/integration/test_upwork_discovery.py \
  tests/integration/test_upwork_session.py tests/integration/test_upwork_connect.py \
  tests/integration/test_upwork_adapter.py tests/unit/test_upwork_token_storage.py
```
