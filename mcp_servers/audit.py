"""Append-only records for MCP write attempts without content or credentials."""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

AuditOutcome = Literal["attempted", "succeeded", "failed"]


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS audit_log ("
                "event_id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "service TEXT NOT NULL, operation TEXT NOT NULL, "
                "request_fingerprint TEXT NOT NULL, "
                "outcome TEXT NOT NULL CHECK (outcome IN ('attempted', 'succeeded', 'failed')))"
            )
            database.execute(
                "CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log "
                "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END"
            )
            database.execute(
                "CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log "
                "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END"
            )

    def record(self, service: str, operation: str, key: str, outcome: AuditOutcome) -> None:
        with closing(sqlite3.connect(self.path)) as database, database:
            database.execute(
                "INSERT INTO audit_log (service, operation, request_fingerprint, outcome) "
                "VALUES (?, ?, ?, ?)",
                (service, operation, fingerprint(key), outcome),
            )
