"""The saved Upwork sign-in is encrypted per user and unreadable without its key."""

import pytest
from cryptography.fernet import Fernet
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from mcp_servers.marketplace.adapters.upwork_auth import (
    EncryptedTokenStorage,
    TokenKeyError,
    load_fernet,
)

TOKENS = OAuthToken(
    access_token="secret-access",
    token_type="Bearer",
    expires_in=3600,
    refresh_token="secret-refresh",
)
CLIENT = OAuthClientInformationFull(
    client_id="desktop-1", redirect_uris=["http://127.0.0.1:8765/oauth/callback"]
)


async def test_tokens_and_client_round_trip_without_plaintext_on_disk(tmp_path):
    storage = EncryptedTokenStorage(
        tmp_path, "alice", Fernet(Fernet.generate_key()), clock=lambda: 1000.0
    )
    assert await storage.get_tokens() is None and not storage.connected()
    await storage.set_client_info(CLIENT)
    await storage.set_tokens(TOKENS)
    storage.set_account({"org_uid": "org-1", "type": "Freelancer"})
    raw = (tmp_path / "alice.oauth").read_bytes()
    assert b"secret-access" not in raw and b"secret-refresh" not in raw and b"org-1" not in raw
    assert (await storage.get_tokens()).refresh_token == "secret-refresh"
    assert (await storage.get_client_info()).client_id == "desktop-1"
    assert storage.expires_at() == 4600.0
    assert storage.get_account()["org_uid"] == "org-1"
    assert storage.disconnect() and not storage.connected()


async def test_users_are_kept_apart(tmp_path):
    key = Fernet(Fernet.generate_key())
    await EncryptedTokenStorage(tmp_path, "alice", key).set_tokens(TOKENS)
    assert await EncryptedTokenStorage(tmp_path, "bob", key).get_tokens() is None


async def test_a_different_key_cannot_read_the_sign_in(tmp_path):
    await EncryptedTokenStorage(tmp_path, "alice", Fernet(Fernet.generate_key())).set_tokens(TOKENS)
    other = EncryptedTokenStorage(tmp_path, "alice", Fernet(Fernet.generate_key()))
    with pytest.raises(TokenKeyError, match="cannot be read"):
        await other.get_tokens()


@pytest.mark.parametrize("user", ["", "../x", "a/b", "x" * 65])
def test_user_names_cannot_escape_the_directory(tmp_path, user):
    with pytest.raises(ValueError):
        EncryptedTokenStorage(tmp_path, user, Fernet(Fernet.generate_key()))


@pytest.mark.parametrize("key", ["", "replace_me", "not-a-key"])
def test_missing_or_bad_keys_are_reported(key):
    with pytest.raises(TokenKeyError):
        load_fernet(key)
