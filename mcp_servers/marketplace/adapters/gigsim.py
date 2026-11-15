"""Marketplace port implementation for GigSim's public HTTP API."""

from __future__ import annotations

from urllib.parse import quote

import httpx
from pydantic import BaseModel, TypeAdapter

from mcp_servers.marketplace.port import (
    ClientHistory,
    ConnectsBalance,
    ConnectsQuote,
    Event,
    Job,
    JobPage,
    MarketStat,
    Message,
    MessageBody,
    PaymentType,
    Proposal,
    ProposalInput,
    Thread,
)


class _MessageInput(BaseModel):
    body: MessageBody


def _path_segment(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("A marketplace resource ID must be one path segment")
    return quote(value, safe="")


class GigSimAdapter:
    def __init__(self, client: httpx.AsyncClient, *, token: str) -> None:
        """Use a caller-owned client configured with GigSim's base URL and timeouts."""
        self._client = client
        self._token = token

    async def _request[T](
        self,
        method: str,
        path: str,
        response_type: type[T],
        *,
        params: dict[str, str | int | float | None] | None = None,
        body: BaseModel | None = None,
        idempotency_key: str | None = None,
    ) -> T:
        headers = {"Authorization": f"Bearer {self._token}"}
        if method == "POST":
            if not idempotency_key or not idempotency_key.strip() or len(idempotency_key) > 128:
                raise ValueError("Writes require an idempotency key of 1 to 128 characters")
            headers["Idempotency-Key"] = idempotency_key
        response = await self._client.request(
            method,
            path,
            params={key: value for key, value in (params or {}).items() if value is not None},
            json=body.model_dump(mode="json") if body is not None else None,
            headers=headers,
            follow_redirects=False,
        )
        response.raise_for_status()
        return TypeAdapter(response_type).validate_json(response.content)

    async def search_jobs(
        self,
        *,
        q: str | None = None,
        domain: str | None = None,
        payment_type: PaymentType | None = None,
        min_budget: float | None = None,
        posted_after: str | None = None,
        limit: int = 20,
        cursor: int = 0,
    ) -> JobPage:
        return await self._request(
            "GET",
            "/jobs",
            JobPage,
            params={
                "q": q,
                "domain": domain,
                "payment_type": payment_type,
                "min_budget": min_budget,
                "posted_after": posted_after,
                "limit": limit,
                "cursor": cursor,
            },
        )

    async def get_job(self, job_id: str) -> Job:
        return await self._request("GET", f"/jobs/{_path_segment(job_id)}", Job)

    async def get_client_history(self, client_id: str) -> ClientHistory:
        return await self._request(
            "GET", f"/clients/{_path_segment(client_id)}/history", ClientHistory
        )

    async def get_connects_balance(self) -> ConnectsBalance:
        return await self._request("GET", "/me/connects", ConnectsBalance)

    async def quote_connects(self, job_id: str) -> ConnectsQuote:
        return await self._request(
            "GET", f"/jobs/{_path_segment(job_id)}/connects-quote", ConnectsQuote
        )

    async def get_market_stats(
        self,
        *,
        domain: str | None = None,
        experience_level: str | None = None,
        payment_type: PaymentType | None = None,
    ) -> list[MarketStat]:
        return await self._request(
            "GET",
            "/market/stats",
            list[MarketStat],
            params={
                "domain": domain,
                "experience_level": experience_level,
                "payment_type": payment_type,
            },
        )

    async def list_proposals(self) -> list[Proposal]:
        return await self._request("GET", "/proposals", list[Proposal])

    async def get_proposal(self, proposal_id: str) -> Proposal:
        return await self._request("GET", f"/proposals/{_path_segment(proposal_id)}", Proposal)

    async def list_threads(self) -> list[Thread]:
        return await self._request("GET", "/threads", list[Thread])

    async def list_messages(self, thread_id: str) -> list[Message]:
        return await self._request(
            "GET", f"/threads/{_path_segment(thread_id)}/messages", list[Message]
        )

    async def list_events(self, *, since: int = 0, limit: int = 100) -> list[Event]:
        return await self._request(
            "GET", "/events", list[Event], params={"since": since, "limit": limit}
        )

    async def create_proposal_draft(
        self, proposal: ProposalInput, *, idempotency_key: str
    ) -> Proposal:
        return await self._request(
            "POST", "/proposals", Proposal, body=proposal, idempotency_key=idempotency_key
        )

    async def submit_proposal(self, proposal_id: str, *, idempotency_key: str) -> Proposal:
        return await self._request(
            "POST",
            f"/proposals/{_path_segment(proposal_id)}/submit",
            Proposal,
            idempotency_key=idempotency_key,
        )

    async def send_message(
        self, thread_id: str, body: MessageBody, *, idempotency_key: str
    ) -> Message:
        return await self._request(
            "POST",
            f"/threads/{_path_segment(thread_id)}/messages",
            Message,
            body=_MessageInput(body=body),
            idempotency_key=idempotency_key,
        )
