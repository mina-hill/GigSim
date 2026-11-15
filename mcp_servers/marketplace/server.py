"""Expose the marketplace port through the official MCP SDK."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, ValidationError

from agent.config import get_config
from apps.api.settings import load_simulator_account_tokens
from mcp_servers.audit import AuditLog
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
from mcp_servers.marketplace.port import (
    ClientHistory,
    ConnectsBalance,
    ConnectsQuote,
    Event,
    Job,
    JobId,
    JobPage,
    MarketplacePort,
    MarketStat,
    Message,
    MessageBody,
    PaymentType,
    Proposal,
    ProposalInput,
    Thread,
)
from mcp_servers.runtime import ROOT, make_server, serve, server_parser, tool_hints

IdempotencyKey = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[!-~]+$")]
ResourceId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[\w~\-][\w.~\-]*$")]
Count = Annotated[int, Field(ge=0)]


class DraftResult(BaseModel):
    dry_run: bool
    payload: ProposalInput
    quote: ConnectsQuote | None = None
    proposal: Proposal | None = None


class SubmitResult(BaseModel):
    dry_run: bool
    proposal_id: str
    quote: ConnectsQuote | None = None
    proposal: Proposal | None = None


class MessageResult(BaseModel):
    dry_run: bool
    thread_id: str
    body: str
    connects_cost: int = 0
    balance: int | None = None
    balance_after: int | None = None
    message: Message | None = None


async def _upstream[T](request: Awaitable[T]) -> T:
    try:
        return await request
    except httpx.HTTPStatusError as error:
        messages = {
            400: "The marketplace rejected the request.",
            401: "Marketplace account authentication failed.",
            403: "The marketplace account cannot access this resource.",
            404: "Marketplace resource not found.",
            409: "Marketplace state or idempotency conflict. Check the current resource.",
            422: "The marketplace rejected the input under its current policy.",
            429: "The marketplace rate limit was reached. Retry after the backoff.",
        }
        raise ToolError(
            messages.get(error.response.status_code, "Marketplace unavailable.")
        ) from None
    except httpx.RequestError:
        raise ToolError("Marketplace unavailable.") from None
    except ValidationError:
        raise ToolError("The marketplace returned data that does not match its contract.") from None


def create_marketplace_server(port: MarketplacePort, audit: AuditLog) -> FastMCP:
    server = make_server(
        "GigBid Marketplace",
        "mcp-marketplace",
        "Use the synthetic marketplace account. Preview costs before requesting human approval.",
    )
    read = tool_hints(external=True)
    write = tool_hints(write=True, external=True)
    spend = tool_hints(write=True, destructive=True, external=True)

    async def audited[T](operation: str, key: str, request: Callable[[], Awaitable[T]]) -> T:
        audit.record("marketplace", operation, key, "attempted")
        try:
            result = await _upstream(request())
        except ToolError:
            audit.record("marketplace", operation, key, "failed")
            raise
        audit.record("marketplace", operation, key, "succeeded")
        return result

    @server.tool(annotations=read)
    async def search_jobs(
        q: Annotated[str, Field(max_length=200)] | None = None,
        domain: Annotated[str, Field(max_length=80)] | None = None,
        payment_type: PaymentType | None = None,
        min_budget: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None,
        posted_after: Annotated[str, Field(max_length=64)] | None = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        cursor: Count = 0,
    ) -> JobPage:
        """Search open jobs by text, domain, payment type, budget and posting date."""
        return await _upstream(
            port.search_jobs(
                q=q,
                domain=domain,
                payment_type=payment_type,
                min_budget=min_budget,
                posted_after=posted_after,
                limit=limit,
                cursor=cursor,
            )
        )

    @server.tool(annotations=read)
    async def get_job(job_id: JobId) -> Job:
        """Read a posted job, its requirements, client summary and advertised Connects."""
        return await _upstream(port.get_job(job_id))

    @server.tool(annotations=read)
    async def get_client_history(client_id: ResourceId) -> ClientHistory:
        """Read a client's public contract history and ratings."""
        return await _upstream(port.get_client_history(client_id))

    @server.tool(annotations=read)
    async def get_connects_balance() -> ConnectsBalance:
        """Read the configured marketplace account's Connects balance and next refill."""
        return await _upstream(port.get_connects_balance())

    @server.tool(annotations=read)
    async def quote_connects(job_id: JobId) -> ConnectsQuote:
        """Read the applicable policy, current Connects cost and balance after submitting."""
        return await _upstream(port.quote_connects(job_id))

    @server.tool(annotations=read)
    async def get_market_stats(
        domain: Annotated[str, Field(max_length=80)] | None = None,
        experience_level: Annotated[str, Field(max_length=80)] | None = None,
        payment_type: PaymentType | None = None,
    ) -> list[MarketStat]:
        """Read posted price percentiles for a domain, experience level and payment type."""
        return await _upstream(
            port.get_market_stats(
                domain=domain,
                experience_level=experience_level,
                payment_type=payment_type,
            )
        )

    @server.tool(annotations=read)
    async def list_proposals() -> list[Proposal]:
        """List the configured account's drafts and submitted proposals."""
        return await _upstream(port.list_proposals())

    @server.tool(annotations=read)
    async def get_proposal(proposal_id: ResourceId) -> Proposal:
        """Read the current state of one proposal belonging to this account."""
        return await _upstream(port.get_proposal(proposal_id))

    @server.tool(annotations=read)
    async def list_threads() -> list[Thread]:
        """List the configured account's simulated client conversations."""
        return await _upstream(port.list_threads())

    @server.tool(annotations=read)
    async def list_messages(thread_id: ResourceId) -> list[Message]:
        """Read messages in one conversation belonging to this account."""
        return await _upstream(port.list_messages(thread_id))

    @server.tool(annotations=read)
    async def list_events(
        since: Count = 0, limit: Annotated[int, Field(ge=1, le=500)] = 100
    ) -> list[Event]:
        """Read account events after an event ID, ordered by ID."""
        return await _upstream(port.list_events(since=since, limit=limit))

    @server.tool(annotations=write)
    async def create_proposal_draft(
        proposal: ProposalInput,
        idempotency_key: IdempotencyKey,
        dry_run: bool = True,
    ) -> DraftResult:
        """Preview an exact proposal or create its private draft after human approval."""
        if dry_run:
            quote = await _upstream(port.quote_connects(proposal.job_id))
            return DraftResult(dry_run=True, payload=proposal, quote=quote)
        receipt = await audited(
            "create_proposal_draft",
            idempotency_key,
            lambda: port.create_proposal_draft(proposal, idempotency_key=idempotency_key),
        )
        return DraftResult(dry_run=False, payload=proposal, proposal=receipt)

    @server.tool(annotations=spend)
    async def submit_proposal(
        proposal_id: ResourceId,
        idempotency_key: IdempotencyKey,
        dry_run: bool = True,
        approved_quote: ConnectsQuote | None = None,
    ) -> SubmitResult:
        """Preview or submit an approved draft and spend Connects once for its key."""
        existing = await _upstream(port.get_proposal(proposal_id))
        if dry_run or existing.status == "draft":
            quote = await _upstream(port.quote_connects(existing.job_id))
            if dry_run:
                return SubmitResult(dry_run=True, proposal_id=proposal_id, quote=quote)
            if quote.balance_after < 0:
                raise ToolError("The marketplace account has insufficient Connects.")
            if approved_quote and (
                quote.job_id != approved_quote.job_id
                or quote.policy_version != approved_quote.policy_version
                or quote.connects_cost != approved_quote.connects_cost
            ):
                raise ToolError("The approved Connects quote changed. Request a new approval.")
        receipt = await audited(
            "submit_proposal",
            idempotency_key,
            lambda: port.submit_proposal(proposal_id, idempotency_key=idempotency_key),
        )
        return SubmitResult(dry_run=False, proposal_id=proposal_id, proposal=receipt)

    @server.tool(annotations=tool_hints(write=True, destructive=True, external=True))
    async def send_message(
        thread_id: ResourceId,
        body: MessageBody,
        idempotency_key: IdempotencyKey,
        dry_run: bool = True,
    ) -> MessageResult:
        """Preview or deliver an explicitly approved message to a simulated client."""
        if dry_run:
            await _upstream(port.list_messages(thread_id))
            balance = (await _upstream(port.get_connects_balance())).balance
            return MessageResult(
                dry_run=True,
                thread_id=thread_id,
                body=body,
                balance=balance,
                balance_after=balance,
            )
        message = await audited(
            "send_message",
            idempotency_key,
            lambda: port.send_message(thread_id, body, idempotency_key=idempotency_key),
        )
        return MessageResult(dry_run=False, thread_id=thread_id, body=body, message=message)

    return server


def main() -> None:
    parser = server_parser("Run the GigBid Marketplace MCP server.", 8102)
    parser.add_argument(
        "--persona",
        default=os.environ.get("MCP_MARKETPLACE_PERSONA", "fullstack"),
        choices=("ai_ml", "fullstack", "data_analyst"),
    )
    parser.add_argument("--audit-db", type=Path, default=ROOT / ".cache" / "mcp-audit.sqlite3")
    args = parser.parse_args()
    config = get_config()
    if config.marketplace["mode"] != "gigsim":
        raise SystemExit("Configure MARKETPLACE_MODE=gigsim for the local MCP server.")
    token = os.environ.get("GIGBID_SIM_TOKEN") or load_simulator_account_tokens().get(
        f"persona_{args.persona}"
    )
    if not token:
        raise SystemExit("Configure a GigSim token for the selected persona.")

    async def start() -> None:
        async with httpx.AsyncClient(
            base_url=config.marketplace["gigsim"]["base_url"],
            timeout=10,
            trust_env=False,
        ) as client:
            server = create_marketplace_server(
                GigSimAdapter(client, token=token),
                AuditLog(args.audit_db),
            )
            await serve(server, args, "MCP_MARKETPLACE_TOKEN")

    asyncio.run(start())


if __name__ == "__main__":
    main()
