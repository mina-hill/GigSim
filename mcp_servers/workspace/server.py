"""Local scheduling and email draft MCP tools for the synthetic workspace."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import AwareDatetime, BaseModel, Field, model_validator

from mcp_servers.audit import AuditLog, fingerprint
from mcp_servers.runtime import ROOT, make_server, run_server, server_parser, tool_hints

IdempotencyKey = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[!-~]+$")]
DraftKind = Literal["hold", "email"]


class Interval(BaseModel):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def order_and_limit(self) -> Interval:
        delta = self.end.astimezone(UTC) - self.start.astimezone(UTC)
        if not timedelta(0) < delta <= timedelta(days=31):
            raise ValueError("Use a positive time range of at most 31 days.")
        return self

    def as_utc(self) -> Interval:
        return Interval(start=self.start.astimezone(UTC), end=self.end.astimezone(UTC))


class Slots(BaseModel):
    timezone: str
    slots: list[Interval]


class HoldDraft(BaseModel):
    slot: Interval
    title: str
    client_ref: str


class EmailDraft(BaseModel):
    to: str
    subject: str
    body: str


class HoldResult(BaseModel):
    dry_run: bool
    payload: HoldDraft
    hold_id: str | None = None


class EmailResult(BaseModel):
    dry_run: bool
    payload: EmailDraft
    draft_id: str | None = None


class WorkspaceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS workspace_drafts ("
                "id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('hold', 'email')), "
                "request_fingerprint TEXT NOT NULL, payload_fingerprint TEXT NOT NULL, "
                "payload TEXT NOT NULL, starts_at TEXT, ends_at TEXT, "
                "UNIQUE(kind, request_fingerprint))"
            )

    def save(self, kind: DraftKind, key: str, payload: HoldDraft | EmailDraft) -> str:
        body = payload.model_dump_json()
        params = (kind, fingerprint(key))
        digest = fingerprint(body)
        start = payload.slot.start.isoformat() if isinstance(payload, HoldDraft) else None
        end = payload.slot.end.isoformat() if isinstance(payload, HoldDraft) else None
        with closing(sqlite3.connect(self.path)) as database, database:
            database.execute("BEGIN IMMEDIATE")
            existing = database.execute(
                "SELECT id, payload_fingerprint FROM workspace_drafts "
                "WHERE kind = ? AND request_fingerprint = ?",
                params,
            ).fetchone()
            if existing:
                if existing[1] != digest:
                    raise ToolError(
                        "The idempotency key was already used with a different payload."
                    )
                return existing[0]
            overlap = (
                database.execute(
                    "SELECT 1 FROM workspace_drafts WHERE kind = 'hold' "
                    "AND starts_at < ? AND ends_at > ? LIMIT 1",
                    (end, start),
                ).fetchone()
                if kind == "hold"
                else None
            )
            if overlap:
                raise ToolError("The calendar already has a hold during this slot.")
            draft_id = uuid4().hex
            database.execute(
                "INSERT INTO workspace_drafts "
                "(id, kind, request_fingerprint, payload_fingerprint, payload, starts_at, ends_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (draft_id, *params, digest, body, start, end),
            )
            return draft_id

    def holds(self, window: Interval) -> list[tuple[datetime, datetime]]:
        window = window.as_utc()
        with closing(sqlite3.connect(self.path)) as database:
            rows = database.execute(
                "SELECT starts_at, ends_at FROM workspace_drafts WHERE kind = 'hold' "
                "AND starts_at < ? AND ends_at > ? ORDER BY starts_at",
                (window.end.isoformat(), window.start.isoformat()),
            ).fetchall()
        return [(datetime.fromisoformat(start), datetime.fromisoformat(end)) for start, end in rows]


def _free_slots(
    window: Interval,
    duration: int,
    zone: ZoneInfo,
    holds: list[tuple[datetime, datetime]],
) -> list[Interval]:
    earliest, latest = window.start.astimezone(UTC), window.end.astimezone(UTC)
    day = earliest.astimezone(zone).date()
    last_day = latest.astimezone(zone).date()
    block = timedelta(minutes=duration)
    step = timedelta(minutes=15)
    slots = []
    while day <= last_day:
        if day.weekday() < 5:
            cursor = datetime.combine(day, time(9), zone).astimezone(UTC)
            close = datetime.combine(day, time(17), zone).astimezone(UTC)
            while cursor + block <= min(close, latest):
                end = cursor + block
                if cursor >= earliest and not any(cursor < b and end > a for a, b in holds):
                    slots.append(Interval(start=cursor.astimezone(zone), end=end.astimezone(zone)))
                cursor += step
        day += timedelta(days=1)
    return slots


def create_workspace_server(workspace: WorkspaceStore, audit: AuditLog) -> FastMCP:
    server = make_server(
        "GigBid Workspace",
        "mcp-workspace",
        "Find local calendar availability and create private, simulated holds and email drafts.",
    )

    def save(kind: DraftKind, operation: str, key: str, payload: HoldDraft | EmailDraft) -> str:
        audit.record("workspace", operation, key, "attempted")
        try:
            draft_id = workspace.save(kind, key, payload)
        except (ToolError, sqlite3.Error):
            audit.record("workspace", operation, key, "failed")
            raise
        audit.record("workspace", operation, key, "succeeded")
        return draft_id

    @server.tool(annotations=tool_hints())
    def free_slots(
        range: Interval,
        duration: Annotated[int, Field(ge=15, le=120)],
        tz: Annotated[str, Field(min_length=1, max_length=64)] = "UTC",
    ) -> Slots:
        """Find calendar slots on weekdays from 09:00 to 17:00 in an IANA time zone."""
        try:
            zone = ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise ToolError("Use a valid IANA time zone, such as UTC or Asia/Karachi.") from None
        return Slots(timezone=tz, slots=_free_slots(range, duration, zone, workspace.holds(range)))

    @server.tool(annotations=tool_hints(write=True))
    def create_tentative_hold(
        slot: Interval,
        title: Annotated[str, Field(min_length=1, max_length=160)],
        client_ref: Annotated[str, Field(min_length=1, max_length=128)],
        idempotency_key: IdempotencyKey,
        dry_run: bool = True,
    ) -> HoldResult:
        """Preview or reserve a private tentative hold, without sending invitations."""
        utc = slot.as_utc()
        if not timedelta(minutes=15) <= utc.end - utc.start <= timedelta(minutes=120):
            raise ToolError("Choose a hold between 15 and 120 minutes.")
        payload = HoldDraft(slot=utc, title=title, client_ref=client_ref)
        if dry_run:
            if workspace.holds(utc):
                raise ToolError("The calendar already has a hold during this slot.")
            return HoldResult(dry_run=True, payload=payload)
        hold_id = save("hold", "create_tentative_hold", idempotency_key, payload)
        return HoldResult(dry_run=False, payload=payload, hold_id=hold_id)

    @server.tool(annotations=tool_hints(write=True))
    def create_email_draft(
        to: Annotated[str, Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")],
        subject: Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^\r\n]+$")],
        body: Annotated[str, Field(min_length=1, max_length=12_000)],
        idempotency_key: IdempotencyKey,
        dry_run: bool = True,
    ) -> EmailResult:
        """Preview or save a local email draft, without sending it to the recipient."""
        payload = EmailDraft(to=to, subject=subject, body=body)
        if dry_run:
            return EmailResult(dry_run=True, payload=payload)
        draft_id = save("email", "create_email_draft", idempotency_key, payload)
        return EmailResult(dry_run=False, payload=payload, draft_id=draft_id)

    return server


def main() -> None:
    parser = server_parser("Run the GigBid Workspace MCP server.", 8103)
    parser.add_argument("--db", type=Path, default=ROOT / ".cache" / "mcp-workspace.sqlite3")
    parser.add_argument("--audit-db", type=Path, default=ROOT / ".cache" / "mcp-audit.sqlite3")
    args = parser.parse_args()
    run_server(
        create_workspace_server(WorkspaceStore(args.db), AuditLog(args.audit_db)),
        args,
        "MCP_WORKSPACE_TOKEN",
    )


if __name__ == "__main__":
    main()
