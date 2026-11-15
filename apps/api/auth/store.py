"""Persist local accounts and sessions without storing passwords or session secrets."""

from __future__ import annotations

import secrets
import sqlite3
import time
from contextlib import closing
from hashlib import scrypt, sha256
from pathlib import Path

from pydantic import BaseModel

from apps.api.schemas.workflow import PersonaId

SESSION_COOKIE = "gigbid_session"
SESSION_SECONDS = 7 * 24 * 60 * 60
_DUMMY_SALT = secrets.token_bytes(16)


class LocalUser(BaseModel):
    id: str
    username: str
    persona: PersonaId


class AccountConflict(Exception):
    """An account already owns the requested username or persona."""


def _password_digest(password: str, salt: bytes) -> bytes:
    return scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**15,
        r=8,
        p=3,
        maxmem=64 * 1024 * 1024,
    )


def _session_digest(token: str) -> str:
    return sha256(token.encode()).hexdigest()


class LocalAuthStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS local_users ("
                "id TEXT PRIMARY KEY, "
                "username TEXT NOT NULL UNIQUE, "
                "persona TEXT NOT NULL UNIQUE, "
                "password_salt BLOB NOT NULL, "
                "password_hash BLOB NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS local_sessions ("
                "token_hash TEXT PRIMARY KEY, "
                "user_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE, "
                "expires_at INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS local_sessions_expires ON local_sessions (expires_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    @staticmethod
    def _user(row: sqlite3.Row) -> LocalUser:
        return LocalUser(id=row["id"], username=row["username"], persona=row["persona"])

    def claimed_personas(self) -> set[str]:
        with closing(self._connect()) as db:
            return {row["persona"] for row in db.execute("SELECT persona FROM local_users")}

    def register(self, username: str, password: str, persona: PersonaId) -> LocalUser:
        salt = secrets.token_bytes(16)
        digest = _password_digest(password, salt)
        user = LocalUser(id=secrets.token_hex(16), username=username, persona=persona)
        try:
            with closing(self._connect()) as db, db:
                db.execute(
                    "INSERT INTO local_users (id, username, persona, password_salt, password_hash) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user.id, user.username, user.persona, salt, digest),
                )
        except sqlite3.IntegrityError as error:
            raise AccountConflict("That username or persona is already taken.") from error
        return user

    def authenticate(self, username: str, password: str) -> LocalUser | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM local_users WHERE username = ?", (username,)).fetchone()
        salt = row["password_salt"] if row is not None else _DUMMY_SALT
        digest = _password_digest(password, salt)
        if row is None or not secrets.compare_digest(digest, row["password_hash"]):
            return None
        return self._user(row)

    def start_session(self, user: LocalUser, previous_token: str | None) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM local_sessions WHERE expires_at <= ?", (now,))
            if previous_token:
                db.execute(
                    "DELETE FROM local_sessions WHERE token_hash = ?",
                    (_session_digest(previous_token),),
                )
            db.execute(
                "INSERT INTO local_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (_session_digest(token), user.id, now + SESSION_SECONDS),
            )
        return token

    def session_user(self, token: str | None) -> LocalUser | None:
        if token is None or len(token) != 43:
            return None
        with closing(self._connect()) as db:
            row = db.execute(
                """
                SELECT local_users.id, local_users.username, local_users.persona
                FROM local_users JOIN local_sessions ON local_sessions.user_id = local_users.id
                WHERE local_sessions.token_hash = ? AND local_sessions.expires_at > ?
                """,
                (_session_digest(token), int(time.time())),
            ).fetchone()
        return self._user(row) if row is not None else None

    def end_session(self, token: str | None) -> None:
        if token is None or len(token) != 43:
            return
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM local_sessions WHERE token_hash = ?", (_session_digest(token),))
