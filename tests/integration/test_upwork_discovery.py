"""Drive SDK OAuth and MCP discovery through mock HTTPS responses and a loopback client."""

import asyncio
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from mcp_servers.marketplace.adapters.upwork_discovery import (
    UPWORK_MCP_URL,
    DiscoveryError,
    LoopbackCallback,
    discover,
)

ORIGIN = "https://mcp.upwork.com"
RESOURCE_METADATA = "/.well-known/oauth-protected-resource/mcp"
SDK_PROTOCOL = "2025-11-25"


async def test_mock_upwork_oauth_uses_pkce_and_reads_only_a_paginated_catalog():
    calls = []
    auth = {}

    async def provider(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == RESOURCE_METADATA:
            return httpx.Response(
                200, json={"resource": UPWORK_MCP_URL, "authorization_servers": [ORIGIN]}
            )
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": ORIGIN,
                    "authorization_endpoint": f"{ORIGIN}/authorize",
                    "token_endpoint": f"{ORIGIN}/token",
                    "registration_endpoint": f"{ORIGIN}/register",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                },
            )
        if path == "/register":
            fields = json.loads(request.content)
            assert fields["token_endpoint_auth_method"] == "none"
            assert fields["redirect_uris"][0].startswith("http://127.0.0.1:")
            return httpx.Response(201, json={**fields, "client_id": "test-desktop"})
        if path == "/token":
            form = parse_qs(request.content.decode())
            assert form["client_id"] == ["test-desktop"]
            assert form["code"] == ["fake-code"]
            assert form["redirect_uri"] == [auth["redirect_uri"]]
            digest = hashlib.sha256(form["code_verifier"][0].encode()).digest()
            assert base64.urlsafe_b64encode(digest).decode().rstrip("=") == auth["challenge"]
            return httpx.Response(
                200, json={"access_token": "fake-access", "token_type": "Bearer", "expires_in": 60}
            )
        assert path == "/mcp" and request.method == "POST"
        message = json.loads(request.content)
        if "Authorization" not in request.headers:
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": f'Bearer resource_metadata="{ORIGIN}{RESOURCE_METADATA}"'
                },
                json={"error": "login required"},
            )
        assert request.headers["Authorization"] == "Bearer fake-access"
        method = message["method"]
        calls.append(method)
        if method == "initialize":
            result = {
                "protocolVersion": SDK_PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "Mock provider", "version": "1"},
            }
        elif method == "notifications/initialized":
            return httpx.Response(202)
        else:
            assert method == "tools/list"
            cursor = message.get("params", {}).get("cursor")
            result = {
                "tools": [
                    {
                        "name": "second_fake" if cursor else "first_fake",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                        },
                    }
                ]
            }
            if cursor is None:
                result["nextCursor"] = "page-2"
            else:
                assert cursor == "page-2"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    def create_client(*, headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(provider), headers=headers, timeout=timeout, auth=auth
        )

    def simulate_browser(url: str) -> bool:
        params = parse_qs(urlsplit(url).query)
        assert params["code_challenge_method"] == ["S256"]
        auth["challenge"] = params["code_challenge"][0]
        auth["redirect_uri"] = params["redirect_uri"][0]
        wrong = urlencode({"code": "wrong-code", "state": "wrong-state"})
        correct = urlencode({"code": "fake-code", "state": params["state"][0]})
        with httpx.Client(timeout=3, trust_env=False) as browser:
            assert browser.get(f"{auth['redirect_uri']}?{wrong}").status_code == 400
            received = browser.get(f"{auth['redirect_uri']}?{correct}")
            assert received.status_code == 200
            assert received.headers["Cache-Control"] == "no-store"
            assert "fake-code" not in received.text
            assert browser.get(f"{auth['redirect_uri']}?{correct}").status_code == 409
        return True

    catalog = await discover(
        timeout=10, launch_browser=simulate_browser, httpx_client_factory=create_client
    )
    assert calls == ["initialize", "notifications/initialized", "tools/list", "tools/list"]
    assert [tool["name"] for tool in catalog["tools"]] == ["first_fake", "second_fake"]
    assert "fake-access" not in json.dumps(catalog)


async def test_loopback_callback_rejects_a_non_upwork_consent_url():
    with LoopbackCallback(asyncio.get_running_loop()) as callback:
        with pytest.raises(DiscoveryError, match="official Upwork"):
            await callback.redirect("https://unrelated.invalid/oauth", lambda _: True)
