"""Keep each GigBid user's Upwork sign-in on this computer, encrypted, and reuse it.

The MCP SDK asks a TokenStorage for OAuth tokens and the dynamically registered client. This
storage writes both to one Fernet-encrypted file per user. The key comes from
TOKEN_ENCRYPTION_KEY, so the file alone does not reveal the tokens.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
import webbrowser
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import McpHttpClientFactory, StreamableHttpConnection
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
)

from mcp_servers.marketplace.adapters.upwork_discovery import (
    CALLBACK_PATH,
    UPWORK_MCP_URL,
    LoopbackCallback,
)

DEFAULT_CALLBACK_PORT = 8765
USER_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
PLACEHOLDER_KEYS = {"", "replace_me"}


class TokenKeyError(RuntimeError):
    """The encryption key is missing, malformed, or does not open the saved sign-in."""


class SignInRequired(RuntimeError):
    """The saved sign-in is missing or no longer valid, and this caller cannot open a browser."""


def load_fernet(key: str | None = None) -> Fernet:
    raw = os.environ.get("TOKEN_ENCRYPTION_KEY", "") if key is None else key
    if raw.strip() in PLACEHOLDER_KEYS:
        raise TokenKeyError("Set TOKEN_ENCRYPTION_KEY in .env before connecting Upwork.")
    try:
        return Fernet(raw.strip().encode())
    except ValueError as error:
        raise TokenKeyError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key.") from error


class EncryptedTokenStorage:
    """MCP SDK token storage backed by one encrypted file per GigBid user."""

    def __init__(
        self,
        directory: Path,
        user: str,
        fernet: Fernet,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not USER_PATTERN.fullmatch(user):
            raise ValueError("A GigBid user name may use letters, digits, '.', '_' and '-'.")
        self.directory = directory
        self.path = directory / f"{user}.oauth"
        self._fernet = fernet
        self._clock = clock

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self._fernet.decrypt(self.path.read_bytes()))
        except InvalidToken as error:
            raise TokenKeyError(
                "The saved Upwork sign-in cannot be read with this TOKEN_ENCRYPTION_KEY. "
                "Disconnect and connect again."
            ) from error

    def _save(self, data: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        staged = self.path.with_suffix(".tmp")
        staged.write_bytes(self._fernet.encrypt(json.dumps(data).encode()))
        with_owner_only(staged)
        os.replace(staged, self.path)

    def _update(self, **fields: Any) -> None:
        data = self._load()
        data.update(fields)
        self._save(data)

    async def get_tokens(self) -> OAuthToken | None:
        tokens = self._load().get("tokens")
        return OAuthToken.model_validate(tokens) if tokens else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        expires_at = self._clock() + tokens.expires_in if tokens.expires_in else None
        self._update(
            tokens=tokens.model_dump(mode="json", exclude_none=True), expires_at=expires_at
        )

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        client = self._load().get("client")
        return OAuthClientInformationFull.model_validate(client) if client else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._update(client=client_info.model_dump(mode="json", exclude_none=True))

    def expires_at(self) -> float | None:
        return self._load().get("expires_at")

    def get_oauth_metadata(self) -> OAuthMetadata | None:
        metadata = self._load().get("oauth_metadata")
        return OAuthMetadata.model_validate(metadata) if metadata else None

    def set_oauth_metadata(self, metadata: OAuthMetadata) -> None:
        self._update(oauth_metadata=metadata.model_dump(mode="json", exclude_none=True))

    def get_account(self) -> dict[str, Any] | None:
        return self._load().get("account")

    def set_account(self, account: dict[str, Any]) -> None:
        self._update(account=account)

    def forget_client(self) -> None:
        data = self._load()
        if data.pop("client", None) is not None:
            data.pop("tokens", None)
            data.pop("expires_at", None)
            self._save(data)

    def connected(self) -> bool:
        return bool(self._load().get("tokens"))

    def disconnect(self) -> bool:
        """Forget this user's saved sign-in. Returns whether there was one."""
        if not self.path.exists():
            return False
        self.path.unlink()
        return True


def with_owner_only(path: Path) -> None:
    """Restrict the file to its owner where the filesystem supports POSIX modes."""
    try:
        path.chmod(0o600)
    except OSError:
        pass


class SavedSignInProvider(OAuthClientProvider):
    """Restore what a saved sign-in needs so an expired token is refreshed, not re-prompted.

    The SDK treats a loaded token without an expiry as valid and, on a 401, starts a new
    browser sign-in. It also refreshes against "/token" on the server's origin unless it has
    the authorization server's metadata. Saving the expiry and the metadata lets a later run
    refresh at the real token endpoint first.
    """

    async def _initialize(self) -> None:
        await super()._initialize()
        storage = self.context.storage
        if not isinstance(storage, EncryptedTokenStorage):
            return
        if self.context.current_tokens:
            self.context.token_expiry_time = storage.expires_at()
        if self.context.oauth_metadata is None:
            self.context.oauth_metadata = storage.get_oauth_metadata()


def callback_url(port: int) -> str:
    return f"http://127.0.0.1:{port}{CALLBACK_PATH}"


@contextlib.asynccontextmanager
async def upwork_session(
    storage: EncryptedTokenStorage,
    *,
    interactive: bool,
    url: str = UPWORK_MCP_URL,
    port: int = DEFAULT_CALLBACK_PORT,
    timeout: float = 180,
    launch_browser: Callable[[str], bool] = webbrowser.open,
    httpx_client_factory: McpHttpClientFactory | None = None,
) -> AsyncIterator[ClientSession]:
    """Open an MCP session on the official Upwork server with the user's saved sign-in.

    Interactive callers (the connect command) may open Upwork's consent page in the browser.
    Servers and the worker pass interactive=False: when the saved sign-in no longer works they
    raise SignInRequired instead of waiting for a browser nobody is watching.
    """
    redirect_uri = callback_url(port)
    client = await storage.get_client_info()
    if client and [str(u) for u in client.redirect_uris or []] != [redirect_uri]:
        storage.forget_client()

    async with contextlib.AsyncExitStack() as stack:
        if interactive:
            callback = stack.enter_context(LoopbackCallback(asyncio.get_running_loop(), port=port))

            async def redirect(authorization_url: str) -> None:
                await callback.redirect(authorization_url, launch_browser)

            wait_for_code = callback.wait_for_code
        else:

            async def redirect(authorization_url: str) -> None:
                raise SignInRequired("Upwork sign-in expired. Run the Upwork connect command.")

            async def wait_for_code() -> tuple[str, str | None]:
                raise SignInRequired("Upwork sign-in expired. Run the Upwork connect command.")

        provider = SavedSignInProvider(
            server_url=url,
            client_metadata=OAuthClientMetadata(
                client_name="GigBid",
                redirect_uris=[redirect_uri],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="none",  # noqa: S106
            ),
            storage=storage,
            redirect_handler=redirect,
            callback_handler=wait_for_code,
            timeout=timeout,
        )
        connection = StreamableHttpConnection(
            transport="streamable_http",
            url=url,
            auth=provider,
            timeout=min(30, timeout),
            sse_read_timeout=timeout,
            terminate_on_close=False,
            httpx_client_factory=httpx_client_factory,
        )
        session = await stack.enter_async_context(
            MultiServerMCPClient({"upwork": connection}).session("upwork")
        )
        if provider.context.oauth_metadata is not None:
            storage.set_oauth_metadata(provider.context.oauth_metadata)
        yield session
