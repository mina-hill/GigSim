"""Common startup and authentication for the three local MCP processes."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ROOT = Path(__file__).resolve().parent.parent


def tool_hints(
    *, write: bool = False, destructive: bool = False, external: bool = False
) -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=not write,
        destructiveHint=destructive,
        idempotentHint=True,
        openWorldHint=external,
    )


def make_server(name: str, service: str, instructions: str) -> FastMCP:
    allowed_hosts = ["127.0.0.1", "[::1]", "localhost", service]
    return FastMCP(
        name,
        instructions=instructions,
        stateless_http=True,
        json_response=True,
        log_level="WARNING",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[host for name in allowed_hosts for host in (name, f"{name}:*")],
            allowed_origins=[f"http://{host}:*" for host in ("localhost", "127.0.0.1", "[::1]")],
        ),
    )


def authenticated_http(server: FastMCP, token: str) -> ASGIApp:
    """Guard the SDK's transport using a configured bearer secret, including initialize."""
    placeholders = {"", "replace_me"}
    if token in placeholders or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError("A non-placeholder bearer token is required for HTTP transport.")
    app = server.streamable_http_app()
    expected = token.encode()

    async def dispatch(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            auth = Headers(scope=scope).getlist("authorization")
            scheme, _, presented = auth[0].partition(" ") if len(auth) == 1 else ("", "", "")
            if scheme.casefold() != "bearer" or not hmac.compare_digest(
                presented.encode(), expected
            ):
                response = JSONResponse(
                    {"detail": "Bearer authentication required."},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await app(scope, receive, send)

    return dispatch


def server_parser(description: str, port: int) -> argparse.ArgumentParser:
    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--transport", choices=("stdio", "http", "streamable-http"), default="stdio"
    )
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 (container bind; HTTP is protected)
    parser.add_argument("--port", type=int, default=port)
    return parser


async def serve(server: FastMCP, args: argparse.Namespace, token_env: str) -> None:
    if args.transport == "stdio":
        await server.run_stdio_async()
        return
    try:
        app = authenticated_http(server, os.environ.get(token_env, ""))
    except ValueError as error:
        raise SystemExit(f"Set {token_env} before starting HTTP transport.") from error
    config = uvicorn.Config(app, host=args.host, port=args.port, access_log=False)
    await uvicorn.Server(config).serve()


def run_server(server: FastMCP, args: argparse.Namespace, token_env: str) -> None:
    asyncio.run(serve(server, args, token_env))
