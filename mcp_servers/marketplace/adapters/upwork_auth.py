"""Keep each GigBid user's Upwork sign-in on this computer, encrypted, and reuse it.

The MCP SDK asks a TokenStorage for OAuth tokens and the dynamically registered client. This
storage writes both to one Fernet-encrypted file per user. The key comes from
TOKEN_ENCRYPTION_KEY, so the file alone does not reveal the tokens.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

USER_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
PLACEHOLDER_KEYS = {"", "replace_me"}


class TokenKeyError(RuntimeError):
    """The encryption key is missing, malformed, or does not open the saved sign-in."""


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

    def get_account(self) -> dict[str, Any] | None:
        return self._load().get("account")

    def set_account(self, account: dict[str, Any]) -> None:
        self._update(account=account)

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
