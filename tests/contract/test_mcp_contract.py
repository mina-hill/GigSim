from typing import cast

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import LATEST_PROTOCOL_VERSION

from mcp_servers.audit import AuditLog
from mcp_servers.marketplace.port import MarketplacePort
from mcp_servers.marketplace.server import create_marketplace_server
from mcp_servers.portfolio.server import create_portfolio_server
from mcp_servers.runtime import authenticated_http
from mcp_servers.workspace.server import WorkspaceStore, create_workspace_server
from rag.corpus import Evidence


@pytest.fixture
def servers(tmp_path):
    audit = AuditLog(tmp_path / "audit.db")
    corpus = [
        Evidence(
            id="cs-one",
            persona="sample",
            type="case_study",
            source="sample/case.md",
            title="Stripe billing",
            text="Set up Stripe billing and verified webhook signatures.",
            skills=("Stripe",),
        )
    ]
    return {
        "portfolio": create_portfolio_server(corpus),
        "marketplace": create_marketplace_server(cast(MarketplacePort, None), audit),
        "workspace": create_workspace_server(WorkspaceStore(tmp_path / "workspace.db"), audit),
    }


@pytest.mark.parametrize("server_name", ["portfolio", "marketplace", "workspace"])
async def test_list_tools_matches_contract(servers, server_name, snapshot):
    async with create_connected_server_and_client_session(servers[server_name]) as client:
        tools = (await client.list_tools()).tools
    assert tools and all(tool.outputSchema for tool in tools)
    contract = [
        {field: data[field] for field in ("name", "inputSchema", "outputSchema", "annotations")}
        for tool in sorted(tools, key=lambda item: item.name)
        if (data := tool.model_dump(exclude_none=True))
    ]
    assert contract == snapshot(name=server_name)


@pytest.mark.parametrize("server_name", ["portfolio", "marketplace", "workspace"])
async def test_http_transport_requires_token_before_initialize(servers, server_name):
    server = servers[server_name]
    app = authenticated_http(server, "mcp-contract-test")
    headers = {"Accept": "application/json, text/event-stream"}
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "contract-test", "version": "1"},
        },
    }
    async with (
        server.session_manager.run(),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost"
        ) as client,
    ):
        no_token = await client.post("/mcp", json=init, headers=headers)
        wrong_token = await client.post(
            "/mcp",
            json=init,
            headers={
                **headers,
                "Authorization": "Bearer wrong",
            },
        )
        allowed = await client.post(
            "/mcp",
            json=init,
            headers={
                **headers,
                "Authorization": "Bearer mcp-contract-test",
            },
        )
    assert no_token.status_code == wrong_token.status_code == 401
    assert no_token.headers["WWW-Authenticate"] == "Bearer"
    assert allowed.status_code == 200
    assert allowed.json()["result"]["serverInfo"]["name"] == server.name
