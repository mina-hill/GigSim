"""A mock of the official Upwork MCP server: OAuth with PKCE, refresh, and MCP tool calls.

Everything runs through httpx.MockTransport, except the browser step, which really calls
the loopback callback the way Upwork's consent page would redirect to it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

ORIGIN = "https://mcp.upwork.com"
RESOURCE_METADATA = "/.well-known/oauth-protected-resource/mcp"
TOKEN_PATH = "/oauth/token"  # not the SDK's "/token" fallback, so saved metadata matters
PROTOCOL = "2025-11-25"

ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass
class MockUpwork:
    tools: dict[str, ToolHandler] = field(default_factory=dict)
    expires_in: int = 3600
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    grants: list[str] = field(default_factory=list)
    browser_opens: int = 0
    issued: int = 0
    valid_access: set[str] = field(default_factory=set)
    _challenge: str = ""

    def _token(self) -> httpx.Response:
        self.issued += 1
        access = f"access-{self.issued}"
        self.valid_access.add(access)
        return httpx.Response(
            200,
            json={
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": self.expires_in,
                "refresh_token": f"refresh-{self.issued}",
            },
        )

    async def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == RESOURCE_METADATA:
            return httpx.Response(
                200, json={"resource": f"{ORIGIN}/mcp", "authorization_servers": [ORIGIN]}
            )
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": ORIGIN,
                    "authorization_endpoint": f"{ORIGIN}/authorize",
                    "token_endpoint": f"{ORIGIN}{TOKEN_PATH}",
                    "registration_endpoint": f"{ORIGIN}/register",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                },
            )
        if path == "/register":
            fields = json.loads(request.content)
            return httpx.Response(201, json={**fields, "client_id": "gigbid-desktop"})
        if path == TOKEN_PATH:
            form = parse_qs(request.content.decode())
            grant = form["grant_type"][0]
            self.grants.append(grant)
            if grant == "refresh_token" and not form["refresh_token"][0].startswith("refresh-"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            return self._token()
        if path != "/mcp":
            return httpx.Response(404)
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in self.valid_access:
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": f'Bearer resource_metadata="{ORIGIN}{RESOURCE_METADATA}"'
                },
            )
        if request.method != "POST":
            return httpx.Response(405)
        message = json.loads(request.content)
        method = message["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "Mock Upwork", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [{"name": name, "inputSchema": {"type": "object"}} for name in self.tools]
            }
        else:
            assert method == "tools/call", method
            name = message["params"]["name"]
            arguments = message["params"].get("arguments") or {}
            self.calls.append((name, arguments))
            handler = self.tools.get(name)
            if handler is None:
                result = {
                    "content": [{"type": "text", "text": f"Unknown tool {name}"}],
                    "isError": True,
                }
            else:
                value = handler(arguments)
                text = value if isinstance(value, str) else json.dumps(value)
                result = {"content": [{"type": "text", "text": text}], "isError": False}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    def client_factory(self) -> Callable[..., httpx.AsyncClient]:
        def create(*, headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.MockTransport(self.handle),
                headers=headers,
                timeout=timeout,
                auth=auth,
            )

        return create

    def browser(self, url: str) -> bool:
        """Approve on the "consent page": redirect to the loopback callback with a code."""
        self.browser_opens += 1
        params = parse_qs(urlsplit(url).query)
        redirect = params["redirect_uri"][0]
        query = urlencode({"code": "consent-code", "state": params["state"][0]})
        with httpx.Client(timeout=3, trust_env=False) as client:
            assert client.get(f"{redirect}?{query}").status_code == 200
        return True
