"""Typed marketplace contract shared by MCP tools and marketplace adapters."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field

PaymentType = Literal["fixed", "hourly"]
JobId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[\w~\-][\w.~\-]*$")]
MessageBody = Annotated[str, Field(min_length=1, max_length=10_000)]


class MarketplaceError(RuntimeError):
    """An adapter's refusal or upstream failure, with a message that is safe to show a person."""


class ClientSummary(BaseModel):
    client_id: str
    country: str
    payment_verified: bool
    total_spent: float
    rating: float | None
    reviews_count: int
    hire_rate: float | None
    jobs_posted: int
    member_since: str


class Job(BaseModel):
    job_id: str
    title: str
    description: str
    skills: list[str]
    domain: str
    experience_level: str
    payment_type: PaymentType
    budget_min: float | None
    budget_max: float | None
    hourly_min: float | None
    hourly_max: float | None
    duration: str
    screening_questions: list[str]
    applicants: int
    connects_cost: int
    status: str
    posted_at: str
    client: ClientSummary


class JobPage(BaseModel):
    items: list[Job]
    next_cursor: int | None
    now: str


class PastContract(BaseModel):
    title: str
    amount: float
    payment_type: str
    rating_given: float | None
    review: str
    freelancer_feedback: str
    ended_at: str


class ClientHistory(BaseModel):
    client: ClientSummary
    industry: str
    enterprise: bool
    past_contracts: list[PastContract]
    open_jobs: int


class MarketStat(BaseModel):
    domain: str
    experience_level: str
    payment_type: str
    n: int
    p25: float
    p50: float
    p75: float
    unit: str
    source: str


class ConnectsBalance(BaseModel):
    account_id: str
    balance: int
    weekly_refill: int
    next_refill_at: str


class ConnectsQuote(BaseModel):
    job_id: str
    connects_cost: int
    multipliers_applied: list[str]
    balance: int
    balance_after: int
    policy_version: str


class ProposalInput(BaseModel):
    job_id: JobId
    cover_letter: str = Field(min_length=1, max_length=20_000)
    bid_amount: float = Field(gt=0, le=1_000_000)
    bid_type: PaymentType = "fixed"
    screening_answers: list[str] = Field(default_factory=list, max_length=20)
    duration_days: int | None = Field(default=None, ge=1, le=730)


class Proposal(BaseModel):
    proposal_id: str
    job_id: str
    cover_letter: str
    bid_amount: float
    bid_type: str
    screening_answers: list[str]
    status: Literal["draft", "submitted"]
    stage: str
    connects_spent: int
    connects_cost: int
    created_at: str
    submitted_at: str | None


class Thread(BaseModel):
    thread_id: str
    client_id: str
    job_id: str
    job_title: str
    proposal_id: str
    stage: str
    last_message_at: str | None
    messages: int


class Message(BaseModel):
    id: int
    sender: Literal["client", "freelancer"]
    body: str
    sent_at: str


class Event(BaseModel):
    id: int
    type: str
    at: str
    job_id: str | None
    proposal_id: str | None
    thread_id: str | None
    payload: dict[str, Any]


class MarketplacePort(Protocol):
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
    ) -> JobPage: ...

    async def get_job(self, job_id: str) -> Job: ...

    async def get_client_history(self, client_id: str) -> ClientHistory: ...

    async def get_connects_balance(self) -> ConnectsBalance: ...

    async def quote_connects(self, job_id: str) -> ConnectsQuote: ...

    async def get_market_stats(
        self,
        *,
        domain: str | None = None,
        experience_level: str | None = None,
        payment_type: PaymentType | None = None,
    ) -> list[MarketStat]: ...

    async def list_proposals(self) -> list[Proposal]: ...

    async def get_proposal(self, proposal_id: str) -> Proposal: ...

    async def list_threads(self) -> list[Thread]: ...

    async def list_messages(self, thread_id: str) -> list[Message]: ...

    async def list_events(self, *, since: int = 0, limit: int = 100) -> list[Event]: ...

    async def create_proposal_draft(
        self, proposal: ProposalInput, *, idempotency_key: str
    ) -> Proposal: ...

    async def submit_proposal(self, proposal_id: str, *, idempotency_key: str) -> Proposal: ...

    async def send_message(
        self, thread_id: str, body: MessageBody, *, idempotency_key: str
    ) -> Message: ...
