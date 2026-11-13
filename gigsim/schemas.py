"""GigSim's public API contract.

Nothing hidden (preferences, decisions, provenance) appears here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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


class JobOut(BaseModel):
    job_id: str
    title: str
    description: str
    skills: list[str]
    domain: str
    experience_level: str
    payment_type: Literal["fixed", "hourly"]
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
    items: list[JobOut]
    next_cursor: int | None
    now: str


class PastContractOut(BaseModel):
    title: str
    amount: float
    payment_type: str
    rating_given: float | None
    review: str
    freelancer_feedback: str
    ended_at: str


class ClientHistoryOut(BaseModel):
    client: ClientSummary
    industry: str
    enterprise: bool
    past_contracts: list[PastContractOut]
    open_jobs: int


class MarketStatOut(BaseModel):
    domain: str
    experience_level: str
    payment_type: str
    n: int
    p25: float
    p50: float
    p75: float
    unit: str
    source: str


class ConnectsOut(BaseModel):
    account_id: str
    balance: int
    weekly_refill: int
    next_refill_at: str


class ProposalCreate(BaseModel):
    job_id: str = Field(min_length=1, max_length=64)
    cover_letter: str = Field(min_length=1, max_length=20000)
    bid_amount: float = Field(gt=0, le=1_000_000)
    bid_type: Literal["fixed", "hourly"] = "fixed"
    screening_answers: list[str] = Field(default_factory=list, max_length=20)
    duration_days: int | None = Field(default=None, ge=1, le=730)


class ProposalOut(BaseModel):
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


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class MessageOut(BaseModel):
    id: int
    sender: Literal["client", "freelancer"]
    body: str
    sent_at: str


class ThreadOut(BaseModel):
    thread_id: str
    client_id: str
    job_id: str
    job_title: str
    proposal_id: str
    stage: str
    last_message_at: str | None
    messages: int


class EventOut(BaseModel):
    id: int
    type: str
    at: str
    job_id: str | None
    proposal_id: str | None
    thread_id: str | None
    payload: dict


class ErrorOut(BaseModel):
    detail: str
