# Portfolio MCP server

Search evidence from one GigBid persona. The server exposes `search_evidence`,
`get_case_study`, and `list_skills`, plus evidence resources and proposal-writing prompts.
The repository includes synthetic corpora for `fullstack`, `ai_ml`, and `data_analyst`.

## Run with Docker

Clone the [GigSim repository](https://github.com/mina-hill/GigSim) and run these commands in
PowerShell from the repository root with Docker running.

```powershell
docker build -f infra/docker/mcp-portfolio.Dockerfile -t gigbid-portfolio .
$env:MCP_PORTFOLIO_TOKEN = [guid]::NewGuid().ToString('N')
docker run --rm -p 127.0.0.1:8101:8101 -e MCP_PORTFOLIO_TOKEN gigbid-portfolio
```

Connect an MCP client with Streamable HTTP at `http://127.0.0.1:8101/mcp`. Set the client's
`Authorization` header to `Bearer` followed by the value of `MCP_PORTFOLIO_TOKEN`.
The image defaults to the `fullstack` corpus. Pass `-e MCP_PORTFOLIO_PERSONA=ai_ml` or
`-e MCP_PORTFOLIO_PERSONA=data_analyst` before the image name to select another corpus.

## Run over stdio

With `uv` installed, configure a local MCP client to launch this command from the repository
root. The client starts and communicates with the server as a child process.

```bash
uv run --locked --extra rag python -m mcp_servers.portfolio.server --transport stdio --persona fullstack
```

## Verify the server

The contract suite covers tool schemas and bearer authentication. The integration test below
checks search, resources, prompts, and corpus scoping with the MCP SDK's in-memory transport.

```bash
uv run --locked --extra rag pytest -q tests/contract/test_mcp_contract.py tests/integration/test_mcp_servers.py -k portfolio
```

For a running Docker instance, connect and call `list_skills` to check the selected corpus.
See the [MCP tools guide](../../docs/TOOLS.md) for the other GigBid servers.
