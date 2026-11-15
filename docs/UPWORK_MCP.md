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

```bash
uv run pytest -q tests/integration/test_upwork_discovery.py \
  tests/integration/test_upwork_session.py tests/integration/test_upwork_connect.py \
  tests/unit/test_upwork_token_storage.py
```
