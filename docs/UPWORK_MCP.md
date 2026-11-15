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

This is a desktop discovery command. Production sign-in, live read mapping, encrypted
per-user token persistence and the separate confirmation flow still need verification or
implementation. Tests cover discovery, the SDK's PKCE value, state and replay validation,
and catalog pagination using a mocked authority and a real local callback. The assignment's
marketplace send flow stays separate from discovery.

```bash
uv run pytest -q tests/integration/test_upwork_discovery.py
```
