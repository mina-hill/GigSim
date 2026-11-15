"""Sign in once, then reuse and refresh the saved Upwork sign-in without a browser."""

import socket

import pytest
from cryptography.fernet import Fernet

from mcp_servers.marketplace.adapters.upwork_auth import (
    EncryptedTokenStorage,
    SignInRequired,
    upwork_session,
)
from mcp_servers.marketplace.adapters.upwork_discovery import UPWORK_MCP_URL
from tests.support.upwork_mock import MockUpwork


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def flatten(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in flatten(inner)]
    return [error, *([error.__cause__] if error.__cause__ else [])]


async def call(storage, mock, *, interactive, port):
    async with upwork_session(
        storage,
        interactive=interactive,
        port=port,
        timeout=10,
        launch_browser=mock.browser,
        httpx_client_factory=mock.client_factory(),
    ) as session:
        result = await session.call_tool("upwork__list_accounts", {})
        return result.content[0].text


@pytest.fixture
def mock():
    return MockUpwork(tools={"upwork__list_accounts": lambda _: {"accounts": [{"org_uid": "o1"}]}})


async def test_first_sign_in_is_saved_and_reused_without_a_browser(tmp_path, mock):
    storage = EncryptedTokenStorage(tmp_path, "alice", Fernet(Fernet.generate_key()))
    port = free_port()
    assert "o1" in await call(storage, mock, interactive=True, port=port)
    assert mock.browser_opens == 1 and storage.connected()
    assert b"access-1" not in storage.path.read_bytes()

    assert "o1" in await call(storage, mock, interactive=False, port=port)
    assert mock.browser_opens == 1 and mock.grants == ["authorization_code"]


async def test_an_expired_token_is_refreshed_at_the_saved_token_endpoint(tmp_path, mock):
    key = Fernet(Fernet.generate_key())
    port = free_port()
    await call(EncryptedTokenStorage(tmp_path, "alice", key), mock, interactive=True, port=port)
    # Reopen the file with a clock that makes the saved token look an hour old.
    stale = EncryptedTokenStorage(tmp_path, "alice", key)
    tokens = await stale.get_tokens()
    stale._clock = lambda: 0.0
    await stale.set_tokens(tokens)

    later = EncryptedTokenStorage(tmp_path, "alice", key)
    assert "o1" in await call(later, mock, interactive=False, port=port)
    assert mock.grants == ["authorization_code", "refresh_token"]
    assert mock.browser_opens == 1
    assert (await later.get_tokens()).access_token == "access-2"


async def test_a_server_without_a_sign_in_asks_for_connect_instead_of_waiting(tmp_path, mock):
    storage = EncryptedTokenStorage(tmp_path, "bob", Fernet(Fernet.generate_key()))
    with pytest.raises(BaseException) as caught:
        await call(storage, mock, interactive=False, port=free_port())
    assert any(isinstance(e, SignInRequired) for e in flatten(caught.value))
    assert mock.browser_opens == 0


async def test_a_client_registered_for_another_port_is_registered_again(tmp_path, mock):
    storage = EncryptedTokenStorage(tmp_path, "alice", Fernet(Fernet.generate_key()))
    await call(storage, mock, interactive=True, port=free_port())
    assert "o1" in await call(storage, mock, interactive=True, port=free_port())
    assert mock.browser_opens == 2


def test_the_default_server_is_the_official_one():
    assert UPWORK_MCP_URL == "https://mcp.upwork.com/mcp"
