"""Inspect the official Upwork MCP tool catalog from an authenticated desktop session."""

import argparse
import asyncio
import json
import logging
import secrets
import threading
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import McpHttpClientFactory, StreamableHttpConnection
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

UPWORK_MCP_URL = "https://mcp.upwork.com/mcp"
CALLBACK_PATH = "/oauth/callback"
TOOL_FIELDS = {"name", "title", "description", "inputSchema", "outputSchema", "annotations"}
MAX_TOOL_PAGES = 20


class DiscoveryError(RuntimeError):
    """An operator-facing error without OAuth secrets or provider response data."""


class MemoryTokenStorage:
    """Keep one desktop discovery session's credentials in process memory."""

    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client = client_info


def _single(parameters: dict[str, list[str]], name: str) -> str | None:
    values = parameters.get(name, [])
    return values[0] if len(values) == 1 and values[0] else None


class LoopbackCallback:
    def __init__(self, loop: asyncio.AbstractEventLoop, port: int = 0) -> None:
        """Listen on 127.0.0.1. Port 0 picks a free port for one-off discovery; a saved sign-in
        uses a fixed port so its registered redirect URI stays valid between runs."""
        self.loop = loop
        self.received: asyncio.Future[tuple[str | None, str]] = loop.create_future()
        self.state: str | None = None
        self.lock = threading.Lock()
        self.consumed = False
        callback = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                self.request.settimeout(3)
                super().setup()

            def log_message(self, format: str, *args: object) -> None:
                # Request lines contain OAuth authorization codes.
                return

            def do_GET(self) -> None:
                status, message = callback.receive(self.path, self.headers.get("Host"))
                body = message.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.host = f"127.0.0.1:{self.server.server_port}"
        self.url = f"http://{self.host}{CALLBACK_PATH}"
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )

    def __enter__(self) -> "LoopbackCallback":
        self.thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def receive(self, target: str, host: str | None) -> tuple[int, str]:
        try:
            request = urlsplit(target)
            if (
                host != self.host
                or request.path != CALLBACK_PATH
                or request.scheme
                or request.netloc
            ):
                return 404, "Callback not found."
            parameters = parse_qs(request.query, keep_blank_values=True, max_num_fields=12)
        except ValueError:
            return 400, "Invalid OAuth callback."

        state = _single(parameters, "state")
        code = _single(parameters, "code")
        error = _single(parameters, "error")
        if (
            not state
            or not state.isascii()
            or bool(code) == bool(error)
            or ("code" in parameters and "error" in parameters)
        ):
            return 400, "Invalid OAuth callback."
        with self.lock:
            if not self.state or not secrets.compare_digest(state, self.state):
                return 400, "Invalid OAuth state."
            if self.consumed:
                return 409, "This callback was already used."
            self.consumed = True
        self.loop.call_soon_threadsafe(self._resolve, code, state)
        if code:
            return 200, "Authorization received. You can close this tab."
        return 200, "Authorization not granted. You can close this tab."

    def _resolve(self, code: str | None, state: str) -> None:
        if not self.received.done():
            self.received.set_result((code, state))

    async def redirect(self, authorization_url: str, launch: Callable[[str], bool]) -> None:
        uri = urlsplit(authorization_url)
        hostname = uri.hostname or ""
        if (
            uri.scheme != "https"
            or (hostname != "upwork.com" and not hostname.endswith(".upwork.com"))
            or uri.username is not None
            or uri.password is not None
        ):
            raise DiscoveryError("The server did not provide an official Upwork sign-in URL.")
        params = parse_qs(uri.query, max_num_fields=20)
        state = _single(params, "state")
        if not state or not state.isascii() or _single(params, "redirect_uri") != self.url:
            raise DiscoveryError("The server did not provide a valid desktop callback.")
        with self.lock:
            self.state = state
        if not await asyncio.to_thread(launch, authorization_url):
            raise DiscoveryError("The sign-in page could not open in the default browser.")

    async def wait_for_code(self) -> tuple[str, str]:
        code, state = await self.received
        if not code:
            raise DiscoveryError("Upwork sign-in was canceled or declined.")
        return code, state


async def discover(
    *,
    timeout: float,
    launch_browser: Callable[[str], bool] = webbrowser.open,
    httpx_client_factory: McpHttpClientFactory | None = None,
) -> dict:
    with LoopbackCallback(asyncio.get_running_loop()) as callback:

        async def redirect(url: str) -> None:
            await callback.redirect(url, launch_browser)

        provider = OAuthClientProvider(
            server_url=UPWORK_MCP_URL,
            client_metadata=OAuthClientMetadata(
                client_name="GigBid local tool discovery",
                redirect_uris=[callback.url],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="none",  # noqa: S106
            ),
            storage=MemoryTokenStorage(),
            redirect_handler=redirect,
            callback_handler=callback.wait_for_code,
            timeout=timeout,
        )
        connection = StreamableHttpConnection(
            transport="streamable_http",
            url=UPWORK_MCP_URL,
            auth=provider,
            timeout=min(30, timeout),
            sse_read_timeout=timeout,
            terminate_on_close=False,
            httpx_client_factory=httpx_client_factory,
        )
        client = MultiServerMCPClient({"upwork": connection})
        catalog = []
        seen_cursors: set[str] = set()
        cursor = None
        async with asyncio.timeout(timeout), client.session("upwork") as session:
            for _ in range(MAX_TOOL_PAGES):
                page = await session.list_tools(cursor=cursor)
                catalog.extend(
                    tool.model_dump(mode="json", include=TOOL_FIELDS, exclude_none=True)
                    for tool in page.tools
                )
                cursor = page.nextCursor
                if not cursor:
                    return {"server": UPWORK_MCP_URL, "operation": "tools/list", "tools": catalog}
                if cursor in seen_cursors:
                    break
                seen_cursors.add(cursor)
        raise DiscoveryError("Upwork returned incomplete tool catalog pagination.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=180, help="Seconds allowed for sign-in.")
    parser.add_argument("--output", type=Path, help="Save tool catalog metadata to a local file.")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout must be between 1 and 600 seconds.")
    for name in ("mcp", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
    try:
        catalog = asyncio.run(discover(timeout=args.timeout))
    except TimeoutError:
        parser.exit(1, "Upwork sign-in or catalog discovery timed out.\n")
    except DiscoveryError as error:
        parser.exit(1, f"{error}\n")
    except Exception:
        # SDK errors can include provider payloads. Keep secrets out of CLI diagnostics.
        parser.exit(1, "Upwork OAuth or tool discovery did not complete.\n")
    result = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(result, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")


if __name__ == "__main__":
    main()
